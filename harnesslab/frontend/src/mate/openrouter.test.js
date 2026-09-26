/* No network anywhere in this file. fetchImpl is injected and the response body is a real
   ReadableStream carrying real SSE bytes, so the parser is exercised against the wire format
   rather than against a stub told what to return. */

import { describe, it, expect } from 'vitest'
import { buildPack, renderUserMessage } from './pack.js'
import { streamChat, OPENROUTER_URL, mapStatus, OpenRouterError, parseFrame, INCLUDE_USAGE,
         MAX_ERROR_BODY_CHARS, MAX_READS } from './openrouter.js'

const PUBLIC = { provenance: 'public', capturedOptIn: false, isStatic: false }
const BEAT = { id: 'b1', kind: 'oracle_split', atStep: 14, facts: {}, headline: 'visible passed, hidden did not' }
const PACK = buildPack(BEAT, {
  run: { dir: 'demo_mock', run_id: 'r-1', steps: 14, visible_pass: true, hidden_pass: false },
  spans: [{ span: 'execute_tool', 'gen_ai.tool.name': 'run_tests', status: 'error', step: 13, result_preview: 'FAILED' }],
}, PUBLIC)

/* A real ReadableStream of UTF-8 bytes, delivered in the chunks the caller names. */
function sseBody(chunks) {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(c) {
      for (const s of chunks) c.enqueue(enc.encode(s))
      c.close()
    },
  })
}

function okResponse(chunks) {
  return { ok: true, status: 200, body: sseBody(chunks) }
}

function delta(t) {
  return 'data: ' + JSON.stringify({ choices: [{ delta: { content: t } }] }) + '\n\n'
}

async function collect(it) {
  const out = []
  for await (const t of it) out.push(t)
  return out
}

describe('streamChat request', () => {
  it('posts to OpenRouter with the four headers the relay already knows', async () => {
    let seen = null
    const fetchImpl = async (url, init) => {
      seen = { url, init }
      return okResponse([delta('ok'), 'data: [DONE]\n\n'])
    }
    await collect(streamChat({
      model: 'google/gemini-flash-3.8', key: 'sk-or-v1-test', system: 'You narrate a run.',
      pack: PACK, fetchImpl,
    }))

    expect(seen.url).toBe(OPENROUTER_URL)
    expect(seen.init.method).toBe('POST')
    expect(seen.init.headers['Content-Type']).toBe('application/json')
    expect(seen.init.headers.Authorization).toBe('Bearer sk-or-v1-test')
    expect(seen.init.headers['HTTP-Referer']).toBe('https://github.com/antonio-mastropaolo/harnesslab')
    expect(seen.init.headers['X-Title']).toBe('harnesslab')
  })

  it('asks for a stream, and carries the system turn and the fenced pack as the user turn', async () => {
    let body = null
    const fetchImpl = async (url, init) => {
      body = JSON.parse(init.body)
      return okResponse(['data: [DONE]\n\n'])
    }
    await collect(streamChat({
      model: 'google/gemini-flash-3.8', key: 'k', system: 'You narrate a run.',
      pack: PACK, nonce: 'cafe1234', fetchImpl,
    }))

    expect(body.stream).toBe(true)
    expect(body.model).toBe('google/gemini-flash-3.8')
    expect(body.max_tokens).toBe(400)
    expect(body.messages.length).toBe(2)
    expect(body.messages[0].role).toBe('system')
    expect(body.messages[0].content).toBe('You narrate a run.')
    expect(body.messages[1].role).toBe('user')
    expect(body.messages[1].content).toBe(renderUserMessage(PACK, { nonce: 'cafe1234' }))
    // No live probe has run (no network in this work), so usage is not requested by default.
    expect(INCLUDE_USAGE).toBe(false)
    expect('usage' in body).toBe(false)
  })

  it('asks for usage only when told to', async () => {
    let body = null
    const fetchImpl = async (url, init) => { body = JSON.parse(init.body); return okResponse(['data: [DONE]\n\n']) }
    await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl, includeUsage: true }))
    expect(body.usage).toEqual({ include: true })
  })

  it('never puts the key in the body', async () => {
    let raw = null
    const fetchImpl = async (url, init) => { raw = init.body; return okResponse(['data: [DONE]\n\n']) }
    await collect(streamChat({ model: 'm', key: 'sk-or-v1-secret', system: 's', pack: PACK, fetchImpl }))
    expect(raw.includes('sk-or-v1-secret')).toBe(false)
  })

  it('refuses before reaching the network when there is no key', async () => {
    let called = false
    const fetchImpl = async () => { called = true; return okResponse([]) }
    let err = null
    try {
      await collect(streamChat({ model: 'm', key: '', system: 's', pack: PACK, fetchImpl }))
    } catch (e) { err = e }
    expect(called).toBe(false)
    expect(err && err.code).toBe('no_key')
  })
})

describe('streamChat stream', () => {
  it('yields each content delta in order', async () => {
    const fetchImpl = async () => okResponse([
      delta('The visible suite '), delta('agreed while the hidden '), delta('one did not.'),
      'data: [DONE]\n\n',
    ])
    const out = await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    expect(out).toEqual(['The visible suite ', 'agreed while the hidden ', 'one did not.'])
    expect(out.join('')).toBe('The visible suite agreed while the hidden one did not.')
  })

  it('skips empty deltas and role-only frames', async () => {
    const fetchImpl = async () => okResponse([
      'data: ' + JSON.stringify({ choices: [{ delta: { role: 'assistant' } }] }) + '\n\n',
      delta(''),
      delta('words'),
      'data: [DONE]\n\n',
    ])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['words'])
  })

  it('fails with a named code when the response carries no body', async () => {
    const fetchImpl = async () => ({ ok: true, status: 200, body: null })
    let err = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    } catch (e) { err = e }
    expect(err && err.code).toBe('bad_response')
  })
})

describe('streamChat chunk boundaries', () => {
  it('reassembles a data line split across two chunks', async () => {
    const fetchImpl = async () => okResponse([
      'data: {"choices":[{"delta":{"content":"the hidden "}}]}\n\ndata: {"choi',
      'ces":[{"delta":{"content":"suite disagreed."}}]}\n\ndata: [DONE]\n\n',
    ])
    const out = await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    expect(out.join('')).toBe('the hidden suite disagreed.')
  })

  it('reassembles a line split one byte at a time', async () => {
    const whole = delta('slow') + 'data: [DONE]\n\n'
    const fetchImpl = async () => okResponse(whole.split(''))
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['slow'])
  })

  it('survives a multi-byte character split across the chunk seam', async () => {
    const enc = new TextEncoder()
    const bytes = enc.encode(delta('cut here →|') + 'data: [DONE]\n\n')
    const seam = bytes.indexOf(0xe2) + 1   // mid-way through the three bytes of →
    const fetchImpl = async () => ({
      ok: true, status: 200,
      body: new ReadableStream({
        start(c) { c.enqueue(bytes.slice(0, seam)); c.enqueue(bytes.slice(seam)); c.close() },
      }),
    })
    const out = await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    expect(out.join('')).toBe('cut here →|')
  })

  it('ignores OpenRouter keepalive comments between frames', async () => {
    const fetchImpl = async () => okResponse([
      ': OPENROUTER PROCESSING\n\n', delta('a'), ': OPENROUTER PROCESSING\n\n', delta('b'),
      'data: [DONE]\n\n',
    ])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['a', 'b'])
  })

  it('handles CRLF line endings', async () => {
    const fetchImpl = async () => okResponse([delta('a').replace(/\n/g, '\r\n'), 'data: [DONE]\r\n\r\n'])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['a'])
  })

  it('stops at [DONE] and ignores anything after it', async () => {
    const fetchImpl = async () => okResponse([delta('a'), 'data: [DONE]\n\n', delta('never')])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['a'])
  })

  it('ends cleanly when the stream closes without a [DONE]', async () => {
    const fetchImpl = async () => okResponse([delta('a'), delta('b')])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['a', 'b'])
  })

  it('yields a final frame that arrives with no trailing newline', async () => {
    const fetchImpl = async () => okResponse([delta('a'), 'data: ' + JSON.stringify({ choices: [{ delta: { content: 'z' } }] })])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['a', 'z'])
  })

  it('refuses a line that never ends rather than buffering it without bound', async () => {
    const enc = new TextEncoder()
    const chunk = enc.encode('x'.repeat(65536))
    let cancelled = false
    const fetchImpl = async () => ({
      ok: true, status: 200,
      body: new ReadableStream({ pull(c) { c.enqueue(chunk) }, cancel() { cancelled = true } }),
    })
    let e = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    } catch (x) { e = x }
    expect(e && e.code).toBe('bad_response')
    expect(cancelled).toBe(true)
  })

  it('skips a frame whose payload is not JSON instead of dying on it', async () => {
    const fetchImpl = async () => okResponse(['data: {oops\n\n', delta('fine'), 'data: [DONE]\n\n'])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['fine'])
  })
})

/* A real fetch Response carries an error body as a ReadableStream on `body`, and that is the only
   thing streamChat reads: res.text() would buffer the whole of whatever the upstream sent in
   order to keep 300 characters of it. */
function errResponse(status, bodyText) {
  return {
    ok: false, status,
    body: bodyText === undefined ? null : sseBody([bodyText]),
  }
}

async function codeFor(res) {
  try {
    await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl: async () => res }))
  } catch (e) { return e }
  return null
}

describe('mapStatus', () => {
  it('separates the four failures a student actually hits', () => {
    expect(mapStatus(401, '').code).toBe('bad_key')
    expect(mapStatus(403, '').code).toBe('bad_key')
    expect(mapStatus(402, '').code).toBe('no_credit')
    expect(mapStatus(404, '').code).toBe('bad_model')
    expect(mapStatus(429, '').code).toBe('rate_limited')
    expect(mapStatus(500, '').code).toBe('upstream')
    expect(mapStatus(503, '').code).toBe('upstream')
  })

  it('reads a 400 that names a model as a bad slug, and one that does not as upstream', () => {
    expect(mapStatus(400, '{"error":{"message":"google/gemini-flash-3.8 is not a valid model ID"}}').code).toBe('bad_model')
    expect(mapStatus(400, '{"error":{"message":"malformed request"}}').code).toBe('upstream')
  })

  it('tells the student the slug is the fix, not the key', () => {
    const e = mapStatus(404, '')
    expect(e.message.toLowerCase().includes('slug')).toBe(true)
    expect(e.message.includes('Settings')).toBe(true)
    expect(e instanceof OpenRouterError).toBe(true)
  })

  it('keeps the status and a bounded slice of the body for the dock', () => {
    const e = mapStatus(429, 'x'.repeat(5000))
    expect(e.status).toBe(429)
    expect(e.detail.length).toBe(300)
  })

  it('four failures are four different sentences', () => {
    const msgs = [401, 404, 429, 500].map(s => mapStatus(s, '').message)
    expect(new Set(msgs).size).toBe(4)
  })
})

describe('streamChat failures', () => {
  it('maps an HTTP failure through mapStatus', async () => {
    expect((await codeFor(errResponse(401))).code).toBe('bad_key')
    expect((await codeFor(errResponse(429))).code).toBe('rate_limited')
    expect((await codeFor(errResponse(404))).code).toBe('bad_model')
    expect((await codeFor(errResponse(502))).code).toBe('upstream')
  })

  it('carries the real API error text through, which is how a wrong slug names itself', async () => {
    const e = await codeFor(errResponse(400, '{"error":{"message":"gemeni-flash is not a valid model ID"}}'))
    expect(e.code).toBe('bad_model')
    expect(e.detail.includes('gemeni-flash')).toBe(true)
  })

  it('survives an error response whose body cannot be read', async () => {
    const e = await codeFor({ ok: false, status: 500, body: null, text: async () => { throw new Error('gone') } })
    expect(e.code).toBe('upstream')
    expect(e.detail).toBe('')
  })

  it('names a dead network as a network failure, not a bad key', async () => {
    const fetchImpl = async () => { throw new TypeError('Failed to fetch') }
    let e = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    } catch (x) { e = x }
    expect(e.code).toBe('network')
    expect(e.message.includes('openrouter.ai')).toBe(true)
    expect(e.detail.includes('Failed to fetch')).toBe(true)
  })

  it('names a stream that breaks mid-beat', async () => {
    const enc = new TextEncoder()
    let n = 0
    const fetchImpl = async () => ({
      ok: true, status: 200,
      body: new ReadableStream({
        pull(c) {
          if (n++ === 0) c.enqueue(enc.encode(delta('half a sentence')))
          else c.error(new TypeError('network error'))
        },
      }),
    })
    const out = []
    let e = null
    try {
      for await (const t of streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) out.push(t)
    } catch (x) { e = x }
    expect(out).toEqual(['half a sentence'])
    expect(e.code).toBe('network')
  })
})

function abortError() {
  const e = new Error('The operation was aborted.')
  e.name = 'AbortError'
  return e
}

describe('streamChat abort', () => {
  it('passes the signal through to fetch', async () => {
    const ac = new AbortController()
    let seen = null
    const fetchImpl = async (u, init) => { seen = init.signal; return okResponse(['data: [DONE]\n\n']) }
    await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, signal: ac.signal, fetchImpl }))
    expect(seen).toBe(ac.signal)
  })

  it('reports an abort during the request as aborted, not as a network failure', async () => {
    const ac = new AbortController()
    const fetchImpl = async () => { ac.abort(); throw abortError() }
    let e = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, signal: ac.signal, fetchImpl }))
    } catch (x) { e = x }
    expect(e.code).toBe('aborted')
  })

  it('reports an abort mid-stream as aborted, keeping what already arrived', async () => {
    const ac = new AbortController()
    const enc = new TextEncoder()
    let n = 0
    const fetchImpl = async () => ({
      ok: true, status: 200,
      body: new ReadableStream({
        pull(c) {
          if (n++ === 0) { c.enqueue(enc.encode(delta('the hidden suite '))) }
          else { ac.abort(); c.error(abortError()) }
        },
      }),
    })
    const out = []
    let e = null
    try {
      for await (const t of streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, signal: ac.signal, fetchImpl })) out.push(t)
    } catch (x) { e = x }
    expect(out).toEqual(['the hidden suite '])
    expect(e.code).toBe('aborted')
  })

  it('refuses before the request when the signal is already aborted', async () => {
    const ac = new AbortController()
    ac.abort()
    let called = false
    const fetchImpl = async () => { called = true; return okResponse(['data: [DONE]\n\n']) }
    let e = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, signal: ac.signal, fetchImpl }))
    } catch (x) { e = x }
    expect(called).toBe(false)
    expect(e.code).toBe('aborted')
  })

  it('releases the reader when the consumer breaks out early', async () => {
    let cancelled = false
    const enc = new TextEncoder()
    const fetchImpl = async () => ({
      ok: true, status: 200,
      body: new ReadableStream({
        pull(c) { c.enqueue(enc.encode(delta('one'))) },
        cancel() { cancelled = true },
      }),
    })
    for await (const t of streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) {
      expect(t).toBe('one')
      break
    }
    await new Promise(r => setTimeout(r, 0))
    expect(cancelled).toBe(true)
  })
})

/* A usage frame as OpenAI-compatible providers emit it: the last data frame before [DONE], with no
   choices to yield. It must reach the meter and must not reach the prose. */
function usageFrame(usage) {
  return 'data: ' + JSON.stringify({ choices: [], usage }) + '\n\n'
}

describe('parseFrame', () => {
  it('separates the four things a frame can carry', () => {
    expect(parseFrame('data: [DONE]')).toEqual({ done: true, text: '', usage: null, error: null })
    expect(parseFrame(': OPENROUTER PROCESSING')).toEqual({ done: false, text: '', usage: null, error: null })
    expect(parseFrame(delta('hi').trim())).toEqual({ done: false, text: 'hi', usage: null, error: null })
  })

  it('normalises a usage block onto the three fields the meter takes', () => {
    const line = usageFrame({ prompt_tokens: 1200, completion_tokens: 84, cost: 0.00031 }).trim()
    expect(parseFrame(line).usage).toEqual({ inputTokens: 1200, outputTokens: 84, costUsd: 0.00031 })
  })

  it('leaves the price out entirely when the provider named none, and never reads it as zero', () => {
    // Absent, not null: budget.noteUsage tells "the provider never mentioned a price" from "the
    // provider says there is no price" by whether the key is present, and sending it always made
    // every priceless frame claim the model has no published price.
    const line = usageFrame({ prompt_tokens: 10, completion_tokens: 2 }).trim()
    expect(parseFrame(line).usage).toEqual({ inputTokens: 10, outputTokens: 2 })
  })

  it('ignores a usage field that is not an object', () => {
    expect(parseFrame('data: ' + JSON.stringify({ choices: [], usage: 'lots' })).usage).toBe(null)
    expect(parseFrame('data: ' + JSON.stringify({ choices: [], usage: null })).usage).toBe(null)
  })

  it('surfaces an in-band error frame instead of reading it as an empty delta', () => {
    const f = parseFrame('data: {"error":{"code":429,"message":"Rate limit exceeded"}}')
    expect(f.error).toEqual({ code: 429, message: 'Rate limit exceeded' })
  })

  it('reads an error frame with no code as a failure all the same', () => {
    expect(parseFrame('data: {"error":{"message":"something went wrong"}}').error)
      .toEqual({ code: 0, message: 'something went wrong' })
  })

  it('does not read a plain delta or a usage frame as an error', () => {
    expect(parseFrame(delta('hi').trim()).error).toBe(null)
    expect(parseFrame('data: ' + JSON.stringify({ choices: [], error: 'oops' })).error).toBe(null)
  })
})

/* OpenRouter cannot change the status line once the headers are out, so a failure that happens
   after the stream opens arrives as a `data:` frame on a 200. Dropped, it becomes a silent empty
   beat: the conductor sees a clean finish, noteSuccess clears the streak, and AUTO_MUTE_AFTER
   never fires on a key that is failing every single call. */
describe('streamChat in-band failures', () => {
  const errFrame = (code, message) =>
    'data: ' + JSON.stringify({ error: { code, message } }) + '\n\n'

  it('fails the beat on an error frame that arrives before any token', async () => {
    const fetchImpl = async () => okResponse([errFrame(429, 'Rate limit exceeded'), 'data: [DONE]\n\n'])
    let e = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    } catch (x) { e = x }
    expect(e).toBeInstanceOf(OpenRouterError)
    expect(e.code).toBe('rate_limited')
    expect(e.detail.includes('Rate limit exceeded')).toBe(true)
  })

  it('fails the beat on an error frame that arrives mid-sentence, keeping what streamed', async () => {
    const fetchImpl = async () => okResponse([delta('the hidden '), errFrame(402, 'Insufficient credits')])
    const out = []
    let e = null
    try {
      for await (const t of streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) out.push(t)
    } catch (x) { e = x }
    expect(out).toEqual(['the hidden '])
    expect(e.code).toBe('no_credit')
  })

  it('maps an in-band code through the same four sentences as an HTTP one', async () => {
    const codeOf = async (c) => {
      const fetchImpl = async () => okResponse([errFrame(c, 'x')])
      try { await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) } catch (e) { return e.code }
      return null
    }
    expect(await codeOf(401)).toBe('bad_key')
    expect(await codeOf(404)).toBe('bad_model')
    expect(await codeOf(502)).toBe('upstream')
  })

  it('fails on an error frame in the unterminated tail as well', async () => {
    const fetchImpl = async () => okResponse(['data: {"error":{"code":429,"message":"rl"}}'])
    let e = null
    try { await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) } catch (x) { e = x }
    expect(e.code).toBe('rate_limited')
  })
})

/* MAX_LINE_CHARS bounds the SSE path against a broken or hostile upstream. The error path has the
   same sink: a 500 with a huge body cost the page the whole body in heap to keep 300 characters. */
describe('streamChat error body', () => {
  it('reads only a bounded prefix of a failure body, however large it is', async () => {
    const enc = new TextEncoder()
    const CHUNK = 1024
    const chunk = enc.encode('z'.repeat(CHUNK))
    let produced = 0
    let cancelled = false
    const fetchImpl = async () => ({
      ok: false,
      status: 500,
      body: new ReadableStream({
        pull(c) { produced += chunk.length; c.enqueue(chunk) },
        cancel() { cancelled = true },
      }),
    })
    let e = null
    try { await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) } catch (x) { e = x }
    // The stream never ends on its own: without a bound this test does not finish at all.
    expect(e.code).toBe('upstream')
    expect(cancelled).toBe(true)
    expect(e.detail.length).toBeLessThanOrEqual(MAX_ERROR_BODY_CHARS)
    expect(produced).toBeLessThanOrEqual(MAX_ERROR_BODY_CHARS + 4 * CHUNK)
  })

  it('keeps a body that arrives split across chunks whole up to the bound', async () => {
    const fetchImpl = async () => ({
      ok: false, status: 400,
      body: sseBody(['{"error":{"message":"gemeni', '-flash is not a valid model ID"}}']),
    })
    let e = null
    try { await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })) } catch (x) { e = x }
    expect(e.code).toBe('bad_model')
    expect(e.detail.includes('gemeni-flash is not a valid model ID')).toBe(true)
  })
})

/* pack.js is meant to be the single place anything crosses. The transport must therefore be
   unable to put a non-allow-listed field on the wire even when a caller hands it one. */
describe('streamChat wire body', () => {
  it('cannot post a field the allow-list does not name, whoever assembled the pack', async () => {
    const hostile = {
      beat: { kind: 'oracle_split', at_step: 1, headline: 'h' },
      run: { dir: 'captured', run_id: 'r-1', harness: { system_prompt: 'SYSTEM PROMPT MUST NOT LEAVE' },
             openrouter_key: 'sk-or-v1-LEAK' },
      evidence: { provenance: 'public',
                  spans: [{ step: 1, tool: 'bash', ok: false, text: 'boom',
                            env: { OPENROUTER_KEY: 'sk-or-v1-LEAK2' } }] },
    }
    let body = null
    const fetchImpl = async (url, init) => { body = init.body; return okResponse(['data: [DONE]\n\n']) }
    await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: hostile, fetchImpl }))
    expect(body.includes('SYSTEM PROMPT MUST NOT LEAVE')).toBe(false)
    expect(body.includes('sk-or-v1-LEAK')).toBe(false)
    expect(body.includes('sk-or-v1-LEAK2')).toBe(false)
    expect(body.includes('r-1')).toBe(true)
  })
})

describe('streamChat usage', () => {
  it('hands the provider figures to the caller and keeps them out of the prose', async () => {
    const seen = []
    const fetchImpl = async () => okResponse([
      delta('the hidden suite disagreed.'),
      usageFrame({ prompt_tokens: 1200, completion_tokens: 84, cost: 0.00031 }),
      'data: [DONE]\n\n',
    ])
    const out = await collect(streamChat({
      model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl, onUsage: (u) => seen.push(u),
    }))
    expect(out).toEqual(['the hidden suite disagreed.'])          // the yield type is unchanged
    expect(out.every(t => typeof t === 'string')).toBe(true)
    expect(seen).toEqual([{ inputTokens: 1200, outputTokens: 84, costUsd: 0.00031 }])
  })

  it('reports usage that arrives in the same frame as the last token', async () => {
    const seen = []
    const fetchImpl = async () => okResponse([
      'data: ' + JSON.stringify({
        choices: [{ delta: { content: 'done.' } }],
        usage: { prompt_tokens: 5, completion_tokens: 1, cost: 0.000004 },
      }) + '\n\n',
      'data: [DONE]\n\n',
    ])
    const out = await collect(streamChat({
      model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl, onUsage: (u) => seen.push(u),
    }))
    expect(out).toEqual(['done.'])
    expect(seen.length).toBe(1)
    expect(seen[0].outputTokens).toBe(1)
  })

  it('says nothing when the provider reported no usage', async () => {
    const seen = []
    const fetchImpl = async () => okResponse([delta('a'), 'data: [DONE]\n\n'])
    await collect(streamChat({
      model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl, onUsage: (u) => seen.push(u),
    }))
    expect(seen).toEqual([])
  })

  it('streams fine when the caller wants no usage at all', async () => {
    const fetchImpl = async () => okResponse([
      delta('a'), usageFrame({ prompt_tokens: 1, completion_tokens: 1 }), 'data: [DONE]\n\n',
    ])
    expect(await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['a'])
  })

  it('does not let a broken onUsage callback kill the beat', async () => {
    const fetchImpl = async () => okResponse([
      delta('a'), usageFrame({ prompt_tokens: 1, completion_tokens: 1 }), 'data: [DONE]\n\n',
    ])
    const out = await collect(streamChat({
      model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl,
      onUsage: () => { throw new Error('the meter blew up') },
    }))
    expect(out).toEqual(['a'])
  })
})

describe('review: a key problem is never reported as a network problem', () => {
  it('refuses a key with a line break in it, before any fetch', async () => {
    let called = false
    const fetchImpl = async () => { called = true; return okResponse(['data: [DONE]\n\n']) }
    let err = null
    try {
      await collect(streamChat({ model: 'm', key: 'sk-or-v1-ab\ncd', system: 's', pack: PACK, fetchImpl }))
    } catch (e) { err = e }
    expect(err).toBeInstanceOf(OpenRouterError)
    expect(err.code).toBe('bad_key')
    expect(called).toBe(false)
  })

  it('keeps accepting a key that is merely padded with spaces', async () => {
    const fetchImpl = async () => okResponse([delta('hi'), 'data: [DONE]\n\n'])
    expect(await collect(streamChat({ model: 'm', key: '  sk-or-v1-abcd  ', system: 's', pack: PACK, fetchImpl })))
      .toEqual(['hi'])
  })
})

describe('review: an in-band error keeps its tailored sentence', () => {
  it('reads a string code the way it reads an HTTP status', () => {
    expect(mapStatus(0, 'invalid_api_key: your key is not valid').code).toBe('bad_key')
    expect(mapStatus(0, 'rate_limit_exceeded').code).toBe('rate_limited')
  })

  it('carries the string code through the frame rather than flattening it to zero', () => {
    const f = parseFrame('data: {"error":{"code":"rate_limit_exceeded","message":"slow down"}}')
    expect(f.error.code).toBe('rate_limit_exceeded')
  })
})

describe('review 2: what the transport actually hands the meter', () => {
  it('omits costUsd entirely when the provider named no cost', () => {
    const f = parseFrame('data: {"usage":{"prompt_tokens":900,"completion_tokens":60}}')
    expect(Object.prototype.hasOwnProperty.call(f.usage, 'costUsd')).toBe(false)
    expect(f.usage.inputTokens).toBe(900)
  })

  it('still carries a cost the provider did name, including zero', () => {
    const f = parseFrame('data: {"usage":{"prompt_tokens":1,"completion_tokens":1,"cost":0}}')
    expect(f.usage.costUsd).toBe(0)
  })

  it('an in-band error with no code keeps the provider its sentence', () => {
    const e = mapStatus(0, 'Provider returned error')
    expect(e.message).toContain('Provider returned error')
    expect(e.message).not.toContain('returned 0')
  })
})

describe('review 3: a status the server gave outranks a word in its body', () => {
  it('reads a 502 forwarding a provider key error as an upstream problem', () => {
    const e = mapStatus(502, 'upstream error: {"code":"invalid_api_key"} from the provider')
    expect(e.code).toBe('upstream')
  })

  it('still reads a real 401 as a key problem, and a named failure with no status', () => {
    expect(mapStatus(401, 'nope').code).toBe('bad_key')
    expect(mapStatus(0, 'invalid_api_key').code).toBe('bad_key')
    expect(mapStatus(429, '').code).toBe('rate_limited')
  })
})

describe('review 4: the meter is told once, and a stream that says nothing is a failure', () => {
  it('reports one usage for a beat, however many times the provider repeats it', async () => {
    const seen = []
    const cumulative = [
      'data: ' + JSON.stringify({ choices: [{ delta: { content: 'a' } }], usage: { prompt_tokens: 100, completion_tokens: 5, cost: 0.001 } }) + '\n\n',
      'data: ' + JSON.stringify({ choices: [{ delta: { content: 'b' } }], usage: { prompt_tokens: 100, completion_tokens: 10, cost: 0.002 } }) + '\n\n',
      'data: [DONE]\n\n',
    ]
    const fetchImpl = async () => okResponse(cumulative)
    await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl, onUsage: (u) => seen.push(u) }))
    expect(seen.length).toBe(1)
    expect(seen[0].costUsd).toBe(0.002)          // the latest figure, not the sum of the repeats
    expect(seen[0].outputTokens).toBe(10)
  })

  it('refuses a 200 whose body is not a stream at all', async () => {
    const fetchImpl = async () => okResponse(['{"choices":[{"message":{"content":"hi"}}]}'])
    let err = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    } catch (e) { err = e }
    expect(err && err.code).toBe('bad_response')
  })

  it('reads a numeric code that arrived as a string', () => {
    expect(mapStatus('429', '').code).toBe('rate_limited')
    expect(mapStatus('401', '').code).toBe('bad_key')
  })
})

describe('review 5: a stream that never says anything is bounded', () => {
  it('gives up instead of reading empty chunks forever', async () => {
    let reads = 0
    const body = { getReader: () => ({ read: async () => { reads++; return { value: new Uint8Array(0), done: false } }, cancel() {}, releaseLock() {} }) }
    const fetchImpl = async () => ({ ok: true, status: 200, body })
    let err = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: PACK, fetchImpl }))
    } catch (e) { err = e }
    expect(err && err.code).toBe('bad_response')
    expect(reads).toBeLessThanOrEqual(MAX_READS + 2)
  }, 30000)
})

describe('review 6: streamChat only ever throws its own error type', () => {
  it('turns a refused pack into a coded error rather than a PackRefused', async () => {
    const forged = { beat: {}, run: { dir: 'captured' },
                     evidence: { provenance: 'captured', spans: [{ text: 'private' }] } }
    let called = false
    const fetchImpl = async () => { called = true; return okResponse(['data: [DONE]\n\n']) }
    let err = null
    try {
      await collect(streamChat({ model: 'm', key: 'k', system: 's', pack: forged, fetchImpl }))
    } catch (e) { err = e }
    expect(err).toBeInstanceOf(OpenRouterError)
    expect(err.code).toBe('refused')
    expect(called).toBe(false)
  })
})

describe('review 6: a 400 is a slug problem only when it says so', () => {
  it('does not blame the slug for a context-length failure', () => {
    const e = mapStatus(400, "This model's maximum context length is 8192 tokens. However, you requested 9000.")
    expect(e.code).not.toBe('bad_model')
  })

  it('still blames the slug when the body names the model as the problem', () => {
    expect(mapStatus(400, 'is not a valid model id').code).toBe('bad_model')
    expect(mapStatus(404, '').code).toBe('bad_model')
  })
})
