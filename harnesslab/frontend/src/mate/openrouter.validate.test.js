import { describe, it, expect } from 'vitest'
import { validateKey, MAX_ERROR_BODY_CHARS } from './openrouter'

/* A Response-shaped object, not a mock of the unit under test. Every assertion is about
   what OUR extraction does with bodies OpenRouter and the things in front of it really
   return. No network anywhere in this file. */
const res = (status, body) => ({ ok: status >= 200 && status < 300, status, text: async () => body })

/* The same, but delivering its body the way a real browser does: through a stream. This is
   the branch that runs in production, so it is the branch the bound is asserted on. */
function streamRes(status, chunks) {
  const enc = new TextEncoder()
  let i = 0
  const counter = { reads: 0 }
  const body = {
    getReader: () => ({
      read: async () => {
        if (i >= chunks.length) return { value: undefined, done: true }
        counter.reads += 1
        return { value: enc.encode(chunks[i++]), done: false }
      },
      cancel: async () => {},
    }),
  }
  return { r: { ok: status >= 200 && status < 300, status, body, text: async () => chunks.join('') }, counter }
}

describe('the first-use check surfaces what OpenRouter actually said', () => {
  it('reports a bad slug in OpenRouter own words, and names it a slug problem', async () => {
    const f = async () => res(400, JSON.stringify({ error: { message: 'google/gemini-flash-3.8 is not a valid model ID', code: 400 } }))
    const out = await validateKey({ key: 'sk-test', model: 'google/gemini-flash-3.8', fetchImpl: f })
    expect(out.ok).toBe(false)
    expect(out.status).toBe(400)
    expect(out.message).toBe('google/gemini-flash-3.8 is not a valid model ID')
    expect(out.code).toBe('bad_model')
  })

  it('reports a bad key in OpenRouter own words, and names it a key problem', async () => {
    const f = async () => res(401, JSON.stringify({ error: { message: 'No auth credentials found', code: 401 } }))
    const out = await validateKey({ key: 'sk-bad', model: 'x/y', fetchImpl: f })
    expect(out.message).toBe('No auth credentials found')
    expect(out.code).toBe('bad_key')
  })

  it('never answers with the string undefined when a gateway returns HTML', async () => {
    const f = async () => res(502, '<html>\n<head><title>502 Bad Gateway</title></head>\n<body>nginx</body>\n</html>')
    const out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: f })
    expect(out.status).toBe(502)
    expect(out.message).not.toContain('undefined')
    expect(out.message).toContain('502 Bad Gateway')
    expect(out.code).toBe('upstream')
  })

  it('falls back to the status when the body is empty', async () => {
    const out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: async () => res(429, '') })
    expect(out.message).toBe('HTTP 429')
    expect(out.code).toBe('rate_limited')
  })

  it('turns a transport failure into a sentence rather than a stack', async () => {
    const f = async () => { throw new TypeError('Failed to fetch') }
    const out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: f })
    expect(out.ok).toBe(false)
    expect(out.code).toBe('network')
    expect(out.message).toContain('Failed to fetch')
  })

  it('does not spend a request on a key that is not there', async () => {
    let calls = 0
    const f = async () => { calls += 1; return res(200, '{}') }
    const out = await validateKey({ key: '   ', model: 'x/y', fetchImpl: f })
    expect(calls).toBe(0)
    expect(out).toEqual({ ok: false, status: 0, code: 'no_key', message: 'no key set' })
  })

  it('asks for one non-streaming token, with the key on the header and never in the body', async () => {
    let seen = null
    const f = async (u, init) => { seen = { u, init }; return res(200, '{}') }
    const out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: f })
    expect(out.ok).toBe(true)
    expect(seen.u).toBe('https://openrouter.ai/api/v1/chat/completions')
    expect(seen.init.method).toBe('POST')
    expect(seen.init.headers.Authorization).toBe('Bearer sk-test')
    const body = JSON.parse(seen.init.body)
    expect(body.stream).toBe(false)
    expect(body.max_tokens).toBe(1)
    expect(body.model).toBe('x/y')
    expect(seen.init.body).not.toContain('sk-test')
  })
})

describe('the failure body is read the way the streaming path reads one', () => {
  it('quotes a streamed error body, which is the shape a real browser delivers', async () => {
    const { r } = streamRes(502, ['<html><title>502 Bad ', 'Gateway</title></html>'])
    const out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: async () => r })
    expect(out.code).toBe('upstream')
    expect(out.message).toContain('502 Bad Gateway')
  })

  it('stops reading a failure body that never ends, rather than buffering all of it', async () => {
    const chunks = Array.from({ length: 40 }, () => 'x'.repeat(1000))   // 40 KB offered
    const { r, counter } = streamRes(500, chunks)
    const out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: async () => r })
    expect(out.code).toBe('upstream')
    expect(counter.reads).toBeLessThanOrEqual(Math.ceil(MAX_ERROR_BODY_CHARS / 1000) + 1)
    expect(counter.reads).toBeLessThan(chunks.length)
  })
})

describe('review: the check refuses what the stream refuses, and survives a broken fetch', () => {
  it('names a key with a line break inside it, instead of blaming the network', async () => {
    /* streamChat already refuses this, for a reason recorded beside it: fetch throws while
       BUILDING the header, synchronously, inside the try -- so the catch reports "could not
       reach openrouter.ai" and the student goes looking at their wifi. The check is the FIRST
       place a wrapped paste arrives, so it is the place that must say it. */
    const thrower = async () => { throw new TypeError('Invalid value') }
    const out = await validateKey({ key: 'sk-or-v1-abc\ndef', model: 'x/y', fetchImpl: thrower })
    expect(out.code).toBe('bad_key')
    expect(out.message).toMatch(/line break/i)
  })

  it('refuses an inner space the same way, since a header cannot carry one either', async () => {
    const out = await validateKey({ key: 'sk-or v1-abc', model: 'x/y', fetchImpl: async () => res(200, '{}') })
    expect(out.code).toBe('bad_key')
    expect(out.ok).toBe(false)
  })

  it('still accepts an ordinary key, so the guard is not refusing everything', async () => {
    const out = await validateKey({ key: '  sk-or-v1-abcdefghijklmnop  ', model: 'x/y', fetchImpl: async () => res(200, '{}') })
    expect(out.ok).toBe(true)
  })

  it('returns a failure when fetch resolves to nothing, rather than throwing out of the check', async () => {
    /* A patched or extension-wrapped fetch can resolve undefined. The caller awaits this inside
       a submit handler, so a throw here is an unhandled rejection and the pane sits on
       "checking…" forever -- the one state that tells the student nothing. */
    let out
    await expect((async () => { out = await validateKey({ key: 'sk-test', model: 'x/y', fetchImpl: async () => undefined }) })())
      .resolves.toBeUndefined()
    expect(out.ok).toBe(false)
    expect(out.code).toBe('bad_response')
    expect(out.message).toMatch(/no response/i)
  })
})
