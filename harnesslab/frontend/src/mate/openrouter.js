/* Transport, and nothing else. This file decides nothing about what is true and nothing about what
   is allowed to be sent -- pack.js already did both. It puts the pack on the wire and hands back
   tokens as they arrive.

   The headers are not rediscovered: core/mate.py already worked out what OpenRouter wants -- the
   Bearer key and the HTTP-Referer / X-Title pair. The one thing mate.py cannot do is stream, which
   is the whole reason this file exists beside it rather than through it. core/mate.py is not
   modified.

   fetchImpl is a parameter so the tests drive a real ReadableStream with no network and no global
   stubbing. */

import { renderUserMessage } from './pack.js'

export const OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
export const REFERER = 'https://github.com/antonio-mastropaolo/harnesslab'
export const TITLE = 'harnesslab'
export const DEFAULT_MAX_TOKENS = 400
/* One SSE line longer than this is not a frame a 400-token beat can produce; buffering it without
   bound would let a broken or hostile upstream grow the page's memory for as long as it likes. */
export const MAX_LINE_CHARS = 1_000_000
/* mapStatus keeps 300 characters of a failure body and no more, so reading the whole of one is
   paid-for-nothing heap. Same threat as MAX_LINE_CHARS -- a broken or hostile upstream, not a
   network attacker -- and the error path had no bound at all. */
export const MAX_ERROR_BODY_CHARS = 4096

/* Both loops above advance only on non-empty data, so a body that enqueues empty chunks forever
   passes every character bound and spins: measured here, a stream of zero-length Uint8Arrays ran
   until the process was killed and starved the event loop -- in a browser that is a frozen tab, not
   a lost beat. A read is a read, whether or not it carried anything. */
export const MAX_READS = 100_000

/* Whether to send `usage: {include: true}`. The plan sets this from a ten-second live probe; that
 * probe did NOT run (this work makes no network calls), so it stays false and the claim is:
 * INFERRED (unproven) -- whether OpenRouter emits a usage frame on a streamed completion for a
 * given account and model is UNPROVEN. parseFrame reads a usage frame wherever it appears and
 * ignores its absence, so false cannot break the request; but "cannot break it" is not "has been
 * shown to report a price".
 *
 * While it stays false the provider is never asked, so no usage frame ever arrives — and the
 * meter now SAYS so rather than printing a measured-looking zero (budget.js, meter.reported).
 * Settling this needs the live probe; it must happen before the dock ships the meter. */
export const INCLUDE_USAGE = false

export class OpenRouterError extends Error {
  constructor(code, message, extra) {
    super(message)
    const e = extra || {}
    this.name = 'OpenRouterError'
    this.code = code
    this.status = e.status || 0
    this.detail = e.detail || ''
  }
}

const detailOf = (e) => String((e && e.message) || e).slice(0, 300)

/* A key a header can actually carry: printable ASCII, no inner space and no line break. A key
   copied from a wrapped line keeps the break inside it, and fetch then throws while BUILDING the
   header -- synchronously -- which reads as a network failure and sends the student to look at
   their wifi. Both the stream and the first-use check ask this same question, from here, so the
   two cannot drift apart: they did, and the check was the half that blamed the network. */
export const KEY_HAS_INNER_WHITESPACE = (key) => !/^[\x21-\x7e]+$/.test(String(key || '').trim())
export const INNER_WHITESPACE_SENTENCE =
  'That key has a space or a line break inside it. Paste it again in Settings, on one line.'

/** streamChat({model, key, system, pack, signal, ...seams}) -> AsyncIterable<string>.
 *  Throws OpenRouterError with a code: no_key | aborted | network | bad_key | no_credit |
 *  bad_model | rate_limited | upstream | bad_response. */
export async function* streamChat({
  model, key, system, pack, signal,
  nonce, maxTokens = DEFAULT_MAX_TOKENS, onUsage,
  includeUsage = INCLUDE_USAGE,
  fetchImpl = (...a) => globalThis.fetch(...a), url = OPENROUTER_URL,
}) {
  if (typeof key !== 'string' || !key.trim()) {
    throw new OpenRouterError('no_key', 'No OpenRouter key in this page. Paste one in Settings; the headlines keep working without it.')
  }
  if (KEY_HAS_INNER_WHITESPACE(key)) {
    throw new OpenRouterError('bad_key', INNER_WHITESPACE_SENTENCE)
  }
  if (signal && signal.aborted) {
    throw new OpenRouterError('aborted', 'Narration cancelled before it started.')
  }

  /* renderUserMessage refuses a pack whose scope was never asserted, and that refusal is a
     PackRefused -- not the OpenRouterError with a `code` this function documents as its only throw.
     A conductor switching on e.code would fall through every case and show the student nothing. */
  let userMessage
  try {
    userMessage = renderUserMessage(pack, nonce ? { nonce } : undefined)
  } catch (e) {
    throw new OpenRouterError('refused',
      'This beat cannot be sent: ' + ((e && e.reason) || 'the pack was refused.'))
  }

  const body = JSON.stringify({
    model,
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: userMessage },
    ],
    temperature: 0.2,
    max_tokens: maxTokens,
    stream: true,
    ...(includeUsage ? { usage: { include: true } } : {}),
  })

  let res
  try {
    res = await fetchImpl(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + key.trim(),
        'HTTP-Referer': REFERER,
        'X-Title': TITLE,
      },
      body,
      signal,
    })
  } catch (e) {
    if (isAbort(e, signal)) throw new OpenRouterError('aborted', 'Narration cancelled.')
    throw new OpenRouterError('network',
      'Could not reach openrouter.ai. Check the connection; the key and the slug are not the problem.',
      { detail: detailOf(e) })
  }

  if (!res || !res.ok) throw mapStatus(res ? res.status : 0, await bodyText(res))
  if (!res.body || typeof res.body.getReader !== 'function') {
    throw new OpenRouterError('bad_response', 'OpenRouter answered without a stream to read.', { status: res.status })
  }

  /* An SSE frame is not a network chunk. The socket splits wherever it likes -- mid-line, and
     mid-character, since TextDecoder is fed raw bytes -- so the tail of each chunk is held in
     `buf` until a newline actually arrives, and the decoder is kept in streaming mode so a UTF-8
     sequence cut across the seam is reassembled rather than mangled. */
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  /* ONE usage report per beat. A provider that repeats a CUMULATIVE usage block in every frame was
     counted once per frame -- $0.001 for a beat read as $0.002 -- and inflated the beat count the
     dock's "priced for N of M" copy divides by. The last block seen is the beat's figure. */
  let latestUsage = null
  let sawFrame = false
  let reads = 0
  try {
    for (;;) {
      let frame
      try {
        frame = await reader.read()
      } catch (e) {
        if (isAbort(e, signal)) throw new OpenRouterError('aborted', 'Narration cancelled mid-beat.')
        throw new OpenRouterError('network', 'The stream broke mid-beat.', { detail: detailOf(e) })
      }
      const { value, done } = frame
      if (done) break
      buf += dec.decode(value, { stream: true })
      let nl
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl)
        buf = buf.slice(nl + 1)
        const f = parseFrame(line)
        if (f.usage) { latestUsage = f.usage; sawFrame = true }
        if (f.error) { sawFrame = true; throw mapStatus(f.error.code, f.error.message) }
        if (f.done) { sawFrame = true; return }
        if (f.text) { sawFrame = true; yield f.text }
      }
      if (buf.length > MAX_LINE_CHARS) {
        throw new OpenRouterError('bad_response', 'OpenRouter sent a line with no end; the beat was stopped.')
      }
      if (++reads > MAX_READS) {
        throw new OpenRouterError('bad_response', 'OpenRouter kept the connection open without sending anything readable.')
      }
    }
    const tail = parseFrame(buf + dec.decode())
    if (tail.usage) { latestUsage = tail.usage; sawFrame = true }
    if (tail.error) { sawFrame = true; throw mapStatus(tail.error.code, tail.error.message) }
    if (tail.text) { sawFrame = true; yield tail.text }
    if (!sawFrame) {
      /* A 200 carrying no stream at all -- plain JSON, or frames that never parse -- yielded an
         empty beat and threw nothing, so the failure streak was CLEARED by a beat that said
         nothing and the auto-mute this channel exists to trigger never fired. */
      throw new OpenRouterError('bad_response', 'OpenRouter answered with no stream to read; the beat said nothing.')
    }
  } finally {
    report(onUsage, latestUsage)
    closeQuietly(reader)
  }
}

/* One SSE line in, the four things it can carry out: the terminator, a content delta, the
   provider's own usage block, and an in-band failure. Anything that carries none of them -- blank
   lines, the `: OPENROUTER PROCESSING` comments, role-only frames -- comes back empty.

   The error channel exists because the status line is already sent by the time the stream opens:
   a failure that happens after the headers cannot be an HTTP status, so it arrives as a `data:`
   frame on a 200. Dropped, it reads as a clean empty beat -- the conductor's noteSuccess clears
   the failure streak and AUTO_MUTE_AFTER never fires on a key that is failing every call. It is
   routed through the same mapStatus as an HTTP failure so the student gets the same sentence.
   That OpenRouter emits this frame shape is INFERRED (unproven): no live call was made.

   Usage is normalised onto exactly the three fields budget.noteUsage takes. A missing price is
   null, never zero: zero is a real figure a free model can legitimately report, and the meter
   latches to counting tokens on null. Whether a usage frame arrives at all is the provider's
   business; see INCLUDE_USAGE for what is and is not proven. */
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k)

export function parseFrame(line) {
  const empty = { done: false, text: '', usage: null, error: null }
  const s = String(line == null ? '' : line).replace(/\r$/, '')
  if (!s || s.startsWith(':') || !s.startsWith('data:')) return empty
  const payload = s.slice(5).trim()
  if (payload === '[DONE]') return { done: true, text: '', usage: null, error: null }
  let piece
  try { piece = JSON.parse(payload) } catch { return empty }
  const choice = piece && Array.isArray(piece.choices) ? piece.choices[0] : null
  const t = choice && choice.delta && choice.delta.content
  const u = piece && piece.usage
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const e = piece && piece.error
  return {
    done: false,
    text: typeof t === 'string' ? t : '',
    /* A provider's in-band code is often a STRING ('rate_limit_exceeded', 'invalid_api_key'), and
       flattening those to 0 threw away the one thing that picks the right sentence. The code is
       carried as it came; mapStatus reads a number or a name. */
    error: (e && typeof e === 'object' && !Array.isArray(e)) ? {
      code: num(e.code) !== null ? num(e.code) : (typeof e.code === 'string' && e.code ? e.code : 0),
      message: typeof e.message === 'string' ? e.message : '',
    } : null,
    /* costUsd is OMITTED when the provider named no cost. budget.noteUsage tells "never mentioned a
       price" from "says there is no price" by whether the key is there, and this is its only real
       producer: always sending the key made every priceless usage frame read as a model with no
       published price -- a measurement nobody took. */
    usage: (u && typeof u === 'object' && !Array.isArray(u)) ? {
      inputTokens: num(u.prompt_tokens) === null ? 0 : num(u.prompt_tokens),
      outputTokens: num(u.completion_tokens) === null ? 0 : num(u.completion_tokens),
      ...(has(u, 'cost') ? { costUsd: num(u.cost) } : {}),
    } : null,
  }
}

/* Four failures a student actually hits, and they must be four sentences. The design promises that
   a wrong slug is "a one-line fix in Settings rather than a debugging session"; that promise lives
   entirely in the bad_model message below.

   OpenRouter answers a nonexistent model with 404, and sometimes with a 400 whose body names the
   model, so both are read as a slug problem.
   Observed on a live call: INFERRED (unproven): no live call was made. */
export function mapStatus(rawStatus, detail) {
  const d = String(detail === undefined || detail === null ? '' : detail).slice(0, 300)
  // A code delivered in-band can arrive as the STRING "429": parseFrame keeps it as it came, and
  // reading only numbers gave it the "not your key and not your slug" sentence.
  const status = (typeof rawStatus === 'string' && /^\d+$/.test(rawStatus.trim()))
    ? Number(rawStatus.trim()) : rawStatus
  const at = { status, detail: d }
  /* The STATUS the server gave decides first, and the body only when there is no status to read.
     Matching names first blamed the student's key for a 502 whose body forwarded a downstream
     provider's `invalid_api_key` -- and two of those mute the narration on a key that is fine. */
  if (status === 401 || status === 403) {
    return new OpenRouterError('bad_key', 'OpenRouter rejected the key. Paste a working key in Settings; nothing else is wrong.', at)
  }
  if (status === 402) {
    return new OpenRouterError('no_credit', 'This OpenRouter key has no credit left. Narration is off until it does.', at)
  }
  // A 400 whose body merely CONTAINS "model" is not a slug problem: OpenRouter forwards
  // "This model's maximum context length is 8192 tokens" for a prompt that is too long, and telling
  // the student to fix a slug that was never wrong costs them the next beat as well.
  if (status === 404 || (status === 400 && /not a valid model|model[_ -]?not[_ -]?found|unknown model|no such model|invalid model/i.test(d))) {
    return new OpenRouterError('bad_model', 'OpenRouter does not know that model slug. Fix the slug in Settings; the key and the connection are fine.', at)
  }
  if (status === 429) {
    return new OpenRouterError('rate_limited', 'OpenRouter is rate limiting this key. Narration backs off and tries the next beat.', at)
  }
  if (status >= 500) {
    return new OpenRouterError('upstream', 'OpenRouter is having trouble. This is not your key and not your slug.', at)
  }
  // No status: an in-band error frame, where the name is all there is (see parseFrame).
  const named = (String(status || '') + ' ' + d).toLowerCase()
  if (/invalid[_ -]?api[_ -]?key|unauthorized|authentication/.test(named)) {
    return new OpenRouterError('bad_key', 'OpenRouter rejected the key. Paste a working key in Settings; nothing else is wrong.', at)
  }
  if (/insufficient[_ -]?(credit|quota|funds)|payment[_ -]?required/.test(named)) {
    return new OpenRouterError('no_credit', 'This OpenRouter key has no credit left. Narration is off until it does.', at)
  }
  if (/rate[_ -]?limit/.test(named)) {
    return new OpenRouterError('rate_limited', 'OpenRouter is rate limiting this key. Narration backs off and tries the next beat.', at)
  }
  if (/model[_ -]?not[_ -]?found|unknown[_ -]?model|no[_ -]?such[_ -]?model/.test(named)) {
    return new OpenRouterError('bad_model', 'OpenRouter does not know that model slug. Fix the slug in Settings; the key and the connection are fine.', at)
  }
  if (!status && d) {
    return new OpenRouterError('upstream', 'OpenRouter could not answer: ' + d, at)
  }
  return new OpenRouterError('upstream', 'OpenRouter returned ' + status + '.', at)
}

/* A bounded prefix of a failure body, never the whole of it. res.text() would buffer everything
   the upstream cares to send in order to produce 300 characters of detail, so the stream is read
   directly and cancelled the moment there is enough. A response with no readable body has no
   detail to give. */
async function bodyText(res) {
  if (!res) return ''
  const body = res.body
  if (!body || typeof body.getReader !== 'function') return ''
  let reader
  try { reader = body.getReader() } catch { return '' }
  const dec = new TextDecoder()
  let out = ''
  try {
    let reads = 0
    while (out.length < MAX_ERROR_BODY_CHARS && reads++ < MAX_READS) {
      const { value, done } = await reader.read()
      if (done) break
      out += dec.decode(value, { stream: true })
    }
    out += dec.decode()
  } catch { /* an unreadable body is no detail, not a second failure */ }
  closeQuietly(reader)
  return out.slice(0, MAX_ERROR_BODY_CHARS)
}

/* A cancelled beat is not a failed one. The dock auto-mutes after two consecutive transport
   failures, and a student who mutes mid-sentence must not spend one of those two on his own
   click. */
function isAbort(e, signal) {
  return !!((e && (e.name === 'AbortError' || e.code === 20)) || (signal && signal.aborted))
}

/* The meter is a side channel. A caller whose accounting throws must not cost the reader the beat
   that was already on screen. */
function report(onUsage, usage) {
  if (!usage || typeof onUsage !== 'function') return
  try { onUsage(usage) } catch { /* the beat matters more than the meter */ }
}

function closeQuietly(reader) {
  try {
    const p = reader.cancel()
    if (p && typeof p.catch === 'function') p.catch(() => { /* already gone */ })
  } catch { /* already gone */ }
}

/** OpenRouter answers a bad slug or a bad key with {"error":{"message":...}}. A gateway
 *  in front of it answers with an HTML page. A rate limiter can answer with nothing at
 *  all. None of those may reach a student as the string "undefined", so every branch
 *  here ends in a sentence.
 *
 *  A real Response carries its body as a stream, and that is read through bodyText, which
 *  stops at MAX_ERROR_BODY_CHARS -- the same bound the streaming path uses, for the same
 *  reason: buffering everything a broken or hostile upstream cares to send in order to
 *  quote 300 characters of it is paid-for-nothing heap. text() is the fallback for a
 *  response shape that has no stream to read. */
async function apiSentence(r) {
  let body = ''
  try {
    body = (r && r.body && typeof r.body.getReader === 'function')
      ? await bodyText(r)
      : String((await r.text()) || '').slice(0, MAX_ERROR_BODY_CHARS)
  } catch { body = '' }
  try {
    const j = JSON.parse(body)
    if (j && j.error && j.error.message) return { message: String(j.error.message), body }
  } catch { /* not JSON: fall through to the raw body */ }
  const trimmed = body.replace(/\s+/g, ' ').trim()
  return { message: trimmed ? trimmed.slice(0, 300) : ('HTTP ' + r.status), body }
}

/** One tiny non-streaming completion, purely to find out whether the key and the slug
 *  are real. The whole point is the failure path: the design ships the slug unverified,
 *  so a generic "request failed" would send a student debugging the wrong layer, while
 *  OpenRouter's own sentence names the actual problem. The CODE beside it comes from
 *  mapStatus, so the settings pane and the dock classify a failure identically.
 *
 *  fetchImpl is injectable so this is testable with no network and no key. */
export async function validateKey({ key, model, fetchImpl = (...a) => globalThis.fetch(...a), url = OPENROUTER_URL }) {
  const k = String(key || '').trim()
  if (!k) return { ok: false, status: 0, code: 'no_key', message: 'no key set' }
  if (KEY_HAS_INNER_WHITESPACE(k)) {
    return { ok: false, status: 0, code: 'bad_key', message: INNER_WHITESPACE_SENTENCE }
  }
  let r
  try {
    r = await fetchImpl(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + k,
        'HTTP-Referer': REFERER,
        'X-Title': TITLE,
      },
      body: JSON.stringify({
        model: String(model || '').trim(),
        messages: [{ role: 'user', content: 'ok' }],
        max_tokens: 1,
        stream: false,
      }),
    })
  } catch (e) {
    const detail = detailOf(e)
    return { ok: false, status: 0, code: 'network', message: 'Could not reach openrouter.ai: ' + detail }
  }
  if (r && r.ok) return { ok: true, status: r.status, code: 'ok', message: '' }
  /* A patched fetch -- an extension, a test double, a polyfill -- can resolve nothing at all.
     Reading .status off it throws out of an async function the pane awaits inside a submit
     handler, which is an unhandled rejection and a pane stuck on "checking..." for good. */
  if (!r) return { ok: false, status: 0, code: 'bad_response', message: 'No response from openrouter.ai.' }
  const { message, body } = await apiSentence(r)
  return { ok: false, status: r.status, code: mapStatus(r.status, body).code, message }
}
