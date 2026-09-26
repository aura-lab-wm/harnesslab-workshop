import { useEffect, useRef, useState, useCallback } from 'react'

/** A file written by `python -m harnesslab --export` carries every read-only response inline.
 *  There is no server to reach, so reads are served from the blob and writes are refused. */
export const STATIC_DATA = typeof window !== 'undefined' ? window.__HARNESSLAB_DATA__ : undefined
export const IS_STATIC = !!STATIC_DATA

export async function api(path, opts = {}) {
  if (IS_STATIC) {
    if (opts.method && opts.method !== 'GET') {
      throw new Error('This is a static export — launching runs and training need the live app.')
    }
    if (path in STATIC_DATA) return STATIC_DATA[path]
    // Match on the normalised query, never on the bare path: a request carrying parameters the
    // snapshot does not have (a second results dir, a picked cell) must NOT quietly resolve to the
    // snapshot taken without them. Returning plausible-but-wrong numbers is the one failure this
    // whole project is about.
    const norm = (u) => {
      const [b, qs] = u.split('?')
      const q = new URLSearchParams(qs || '')
      const kept = [...q.entries()].filter(([, v]) => v !== '' && v != null).sort()
      return b + (kept.length ? '?' + kept.map(([k, v]) => `${k}=${v}`).join('&') : '')
    }
    const want = norm(path)
    const hit = Object.keys(STATIC_DATA).find(k => norm(k) === want)
    if (hit) return STATIC_DATA[hit]
    throw new Error('Not in this static export — that view needs the live app (python -m harnesslab).')
  }
  const r = await fetch('/api' + path, {
    ...opts,
    // merged, not replaced: a caller passing PRIVATE must not silently lose content-type
    headers: { 'content-type': 'application/json', ...(opts.headers || {}) },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  const ct = r.headers.get('content-type') || ''
  return ct.includes('json') ? r.json() : r.text()
}

/** Opts a request in to private results (captured sessions).
 *
 * Nothing gets them by default: the backend answers 404 to any request that does not carry this,
 * so a page that wants captured data has to say so at the call site. That is deliberate -- the
 * boundary should be visible in the code that crosses it, not buried in a default. */
export const PRIVATE = { 'x-harnesslab-private': '1' }

/** Everything the console may ask the watcher for. The same closed vocabulary the route holds
 *  (capture_api.CONTROL_ACTIONS): control.json has two fields and must never grow a third. */
export const CAPTURE_ACTIONS = ['pause', 'resume', 'nudge']

/** Pause the watcher, resume it, or ask it for a pass now.
 *
 * The only write on the capture API, and it writes captured/control.json alone -- the two fields
 * the sniffer reads between passes, and the same two the macOS menubar app writes. It carries the
 * private opt-in like every read on that page, which is also what keeps it off the reach of another
 * page on this machine: a custom header needs a CORS preflight this app never grants.
 *
 * The action is checked here as well as on the route. A typo would otherwise travel to the backend
 * and come back a 422 for the page to explain, when nothing should have been sent at all. */
export function captureControl(action) {
  if (!CAPTURE_ACTIONS.includes(action)) {
    return Promise.reject(new Error(`not something the watcher can be asked: ${action}`))
  }
  return api('/capture/control', { method: 'POST', headers: PRIVATE, body: { action } })
}

export function useFetch(path, deps = [], enabled = true, opts = undefined) {
  const [state, set] = useState({ data: null, error: null, loading: !!path })
  const reload = useCallback(() => {
    if (!path || !enabled) return
    set(s => ({ ...s, loading: true }))
    api(path, opts || {}).then(data => set({ data, error: null, loading: false }))
      .catch(error => set({ data: null, error, loading: false }))
  }, [path, enabled, JSON.stringify(opts || null)]) // eslint-disable-line
  useEffect(() => { reload() }, [reload, ...deps]) // eslint-disable-line
  return { ...state, reload }
}

/** Live event stream from the backend. Keeps a bounded log plus per-run state. */
export function useEvents(enabled = true) {
  const [runs, setRuns] = useState({})       // run_id -> {meta, spans[], summary?}
  const [jobs, setJobs] = useState({})
  const [log, setLog] = useState([])
  const [connected, setConnected] = useState(false)
  const [tick, setTick] = useState(0)
  const ref = useRef(null)
  useEffect(() => {
    if (IS_STATIC || !enabled) return // Package preview and static exports have no live stream.
    const es = new EventSource('/api/events')
    ref.current = es
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.onmessage = (m) => {
      let ev
      try { ev = JSON.parse(m.data) } catch { return }
      if (ev.kind === 'run_start') {
        setRuns(r => ({ ...r, [ev.run_id]: { meta: ev, spans: [], summary: null, started: ev.ts, status: 'running' } }))
      } else if (ev.kind === 'span') {
        setRuns(r => {
          const cur = r[ev.run_id]
          if (!cur) return r
          return { ...r, [ev.run_id]: { ...cur, spans: [...cur.spans, ev.span] } }
        })
      } else if (ev.kind === 'run_end') {
        setRuns(r => r[ev.run_id] ? { ...r, [ev.run_id]: { ...r[ev.run_id], summary: ev.summary, status: 'done' } } : r)
      } else if (ev.kind === 'run_error') {
        setRuns(r => r[ev.run_id] ? { ...r, [ev.run_id]: { ...r[ev.run_id], status: 'error', error: ev.error } } : r)
      } else if (ev.kind === 'job') {
        setJobs(j => ({ ...j, [ev.job.id]: ev.job }))
      }
      // `sentinel_train` and `harness_saved` were dropped with the routes that published them: the
      // training and harness-editor screens are gone, and no path on the server emits either now.
      if (['run_start', 'run_end', 'run_error', 'job', 'real_import', 'judge'].includes(ev.kind)) {
        setLog(l => [...l.slice(-199), ev])
        setTick(t => t + 1)
      }
      if (ev.kind === 'span' && ev.span.span === 'sentinel' && ev.span.action && ev.span.action !== 'none') {
        setLog(l => [...l.slice(-199), ev])
      }
    }
    return () => { es.close(); setConnected(false) }
  }, [enabled])
  return { runs, jobs, log, connected, tick, clear: () => setRuns({}) }
}

/** The URL of one run's ledger stream (live_api.py). Named here so the caller-matcher in
 *  tests_agentlab/test_no_dead_surface.py can see the route is called from reachable code. */
export const runStreamUrl = (dir, runId) => `/api/runs/${encodeURIComponent(dir)}/${encodeURIComponent(runId)}/stream`

const ES_CLOSED = 2   // EventSource.CLOSED: the browser gave up (a non-200 answer) and will not retry

const fresh = () => ({ spans: [], summary: null, phase: 'connecting', reconnecting: false, unreadable: 0, lastSeq: -1 })

/** One run's spans as they are written, over Server-Sent Events.
 *
 *  phase: connecting → live → finished (the `end` event, carrying summary.json) | abandoned (the
 *  server's synthetic status after its idle timeout) | missing (the server refused the stream:
 *  no such run). A dropped connection sets `reconnecting` while the browser retries on its own
 *  with Last-Event-ID (the server resumes from seq + 1), and clears it when the stream reopens.
 *  Spans are deduplicated by seq so a replayed frame never doubles a step. Unreadable ledger
 *  lines are counted once per line number. Nothing is opened when `enabled` is false (static
 *  exports, school mode). */
export function useLiveRun(dir, runId, enabled = true) {
  const [state, set] = useState(fresh)
  useEffect(() => {
    set(fresh())
    if (!enabled || IS_STATIC || !dir || !runId) return
    const es = new EventSource(runStreamUrl(dir, runId))
    const seenBad = new Set()
    es.onopen = () => set(s => ({ ...s, reconnecting: false, phase: s.phase === 'connecting' ? 'live' : s.phase }))
    es.onmessage = (m) => {
      let rec
      try { rec = JSON.parse(m.data) } catch { return }
      if (!rec || typeof rec !== 'object') return
      if (rec.span === 'live_status') {
        if (rec.status === 'unreadable_line') {
          if (!seenBad.has(rec.line)) { seenBad.add(rec.line); set(s => ({ ...s, unreadable: seenBad.size })) }
        } else if (rec.status === 'abandoned') {
          es.close(); set(s => ({ ...s, phase: 'abandoned', reconnecting: false }))
        }
        return
      }
      set(s => {
        const seq = typeof rec.seq === 'number' ? rec.seq : null
        if (seq !== null && seq <= s.lastSeq) return s
        return { ...s, spans: [...s.spans, rec], lastSeq: seq === null ? s.lastSeq : seq, phase: 'live', reconnecting: false }
      })
    }
    es.addEventListener('end', (m) => {
      let summary = null
      try { summary = JSON.parse(m.data) } catch { /* the page falls back to run_detail */ }
      es.close()
      set(s => ({ ...s, summary, phase: 'finished', reconnecting: false }))
    })
    es.onerror = () => {
      if (es.readyState === ES_CLOSED) set(s => (s.phase === 'finished' || s.phase === 'abandoned') ? s : { ...s, phase: 'missing', reconnecting: false })
      else set(s => ({ ...s, reconnecting: true }))
    }
    return () => es.close()
  }, [dir, runId, enabled])
  return state
}

export const fmt = {
  pct: (x, d = 0) => (x == null || Number.isNaN(x)) ? '—' : (x * 100).toFixed(d) + '%',
  num: (x, d = 2) => (x == null || Number.isNaN(x)) ? '—' : Number(x).toFixed(d),
  int: (x) => (x == null) ? '—' : Math.round(x).toLocaleString(),
  usd: (x, d = 4) => (x == null || !Number.isFinite(x)) ? '—' : '$' + Number(x).toFixed(d),
  ms: (x) => x == null ? '—' : (x < 1000 ? x + ' ms' : (x / 1000).toFixed(1) + ' s'),
}

/** A run's total tokens, or null when its source recorded no usage.
 *
 * Adding the two fields directly is the trap: `null + null` is **0** in JavaScript, so an
 * unmeasured run renders as one that consumed nothing — indistinguishable on screen from a real
 * zero. The null is a declaration of ignorance (importers.common.UNMEASURED_FIELDS); keep it one. */
export const tokensOf = (r) => (r == null || r.input_tokens == null || r.output_tokens == null) ? null : r.input_tokens + r.output_tokens

/** Total spend over the runs whose cost WAS recorded, plus how many were not.
 *
 * `n + (cost || 0)` reads an unrecorded cost as free and quietly turns a partial total into what
 * looks like the whole. Keeping the two apart lets the caller show the total and say what it
 * leaves out, the way the capture API and the backfill already carry `unmeasured_runs`. */
export const spendOf = (runs, costOf = (r) => r?.summary?.cost_usd) => {
  const xs = (runs || []).map(costOf)
  return { usd: xs.filter(x => x != null).reduce((a, b) => a + b, 0), unmeasured: xs.filter(x => x == null).length }
}

/** Stable per-harness colour slot (the colour itself comes from the active theme; see ui.jsx useChartTheme). */
export const harnessIndex = (() => {
  const map = {}
  let i = 0
  return (h) => { if (!(h in map)) map[h] = i++; return map[h] }
})()
