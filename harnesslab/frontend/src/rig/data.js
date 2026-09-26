/* ====================================================================================
   Rig · data.js — the live data layer. Every figure in the Rig comes through here.

   CONTRACT
   --------
   * One cache for the whole workbench, keyed by API path (the same path string api() takes,
     e.g. '/results/llma4se_live/runs'). A path is fetched at most once until invalidate()d;
     every component asking for it shares the one request and re-renders when it lands.
   * Static exports (IS_STATIC) are handled by api() itself: reads come from the embedded
     blob, a path the snapshot lacks resolves to `error` ("Not in this static export…"),
     and polling is switched off. Views just render the error state.
   * Every hook returns { data, error, loading, reload }. `data` is null until it arrives;
     `loading` is true only while a request is in flight and nothing is cached yet.
     Hooks accept a null/undefined argument and then do nothing (data null, loading false),
     so they can be called unconditionally.
   * Normalised shapes (same names the prototype's D used):
       Dataset row  { name, runs, harnesses[], models[], tasks[], pass_rate, updated,
                      kind: 'recorded'|'mock' }
       Run          { id, ds, task, harness, model, rep, exit,
                      vis, hid, str,          // true | false | null (null = unknown, never fail)
                      in, out, cost, wall,    // null when not recorded (imports have no ledger
                                              //   cost: cost/wall are null there, never 0)
                      steps, tools, edits, bnd, bkinds[], tests, rtbs, started, error,
                      mode,                   // outcomes mode id (hidden suite) or null
                      cutoffCalls, lastFinish, hasLedger, raw }  // raw = the /runs record
   * Pure helpers (verdict, tally, tokStats, fmt …) are exported for views; use them rather
     than re-deriving, so every screen counts unknowns the same way.
   ==================================================================================== */
import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'
import { api, IS_STATIC, PRIVATE } from '../api'

/* ------------------------------------------------------------------ cache core */
const cache = new Map()          // key -> { status, data, error, promise }
const listeners = new Set()
let version = 0
const emit = () => { version++; for (const l of [...listeners]) l() }
const subscribe = (l) => { listeners.add(l); return () => listeners.delete(l) }
const keyOf = (path, opts) => (opts && opts.private ? 'private:' : '') + path

/* ------------------------------------------------------------------ connection
   Whether the local server answers. fetch() rejects with a TypeError when nothing answers at all
   (server stopped, laptop asleep); any HTTP answer, even a 404, means the server is up. The jobs
   poll (every 5 s) doubles as the heartbeat. When the server comes back, every path that failed
   while it was down is dropped, so the mounted views refetch on their own: restarting
   `python -m harnesslab` heals an open workbench with no reload. */
let online = true
const unreachable = (e) => e instanceof TypeError
function markConnection(up) {
  if (up === online) return
  online = up
  if (up) for (const [k, e] of [...cache.entries()]) if (e.status === 'error') cache.delete(k)
  emit()
}
const settle = (error) => markConnection(!(error && unreachable(error)))
/** true while the server answers; false after a request found nobody listening. */
export const isOnline = () => online
export function useConnection() { return useSyncExternalStore(subscribe, isOnline, () => true) }

/** Fetch (once) and cache. Always resolves: with the data, or undefined on error (the error is
 *  in the cache for hooks to render). Safe to fire-and-forget for prefetching. */
export function load(path, opts = {}) {
  const key = keyOf(path, opts)
  const hit = cache.get(key)
  if (hit && (hit.status === 'ok' || hit.status === 'loading')) return hit.promise.then((d) => d, () => undefined)
  const promise = api(path, opts.private ? { headers: PRIVATE } : {})
  const entry = { status: 'loading', data: hit ? hit.data : null, error: null, promise }
  cache.set(key, entry)
  emit()
  promise.then(
    (data) => { settle(null); if (cache.get(key) === entry) { cache.set(key, { status: 'ok', data, error: null, promise }); emit() } },
    (error) => { settle(error); if (cache.get(key) === entry) { cache.set(key, { status: 'error', data: null, error, promise }); emit() } },
  )
  return promise.catch(() => undefined).then(() => { const e = cache.get(key); return e && e.status === 'ok' ? e.data : undefined })
}

/** Re-render on ANY cache change (the shell uses it so title()/ctx() see newly loaded data). */
export function useCacheVersion() { return useSyncExternalStore(subscribe, () => version, () => 0) }

/** The cached value for a path, or undefined. Synchronous; use in title() functions. */
export function peek(path, opts) {
  const e = cache.get(keyOf(path, opts))
  return e && e.status === 'ok' ? e.data : undefined
}

/** Drop cached paths starting with `prefix` (all when omitted); mounted hooks refetch. */
export function invalidate(prefix = '') {
  for (const k of [...cache.keys()]) if (k.replace(/^private:/, '').startsWith(prefix)) cache.delete(k)
  emit()
}

/** Tests: seed responses so views render without a network. `{ '/overview': {...} }`. */
export function prime(map) {
  for (const [path, data] of Object.entries(map)) cache.set(path, { status: 'ok', data, error: null, promise: Promise.resolve(data) })
  emit()
}
/** Tests: seed a failure for a path. */
export function primeError(path, message = 'failed') {
  const error = new Error(message)
  const promise = Promise.reject(error); promise.catch(() => {})
  cache.set(path, { status: 'error', data: null, error, promise })
  emit()
}
export function resetCache() { cache.clear(); online = true; emit() }

const NONE = { status: 'none', data: null, error: null }

/** Generic hook: any GET path. opts: { private: true } adds the private-results header;
 *  { poll: ms } refetches on an interval (never in a static export). */
export function useApi(path, opts = {}) {
  const key = path ? keyOf(path, opts) : null
  const entry = useSyncExternalStore(subscribe, () => (key ? cache.get(key) || NONE : NONE), () => NONE)
  useEffect(() => {
    if (!path) return
    const e = cache.get(key)
    if (!e) load(path, opts)
  }, [key, entry]) // eslint-disable-line react-hooks/exhaustive-deps
  const reload = useCallback(() => { if (path) { cache.delete(key); load(path, opts) } }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const poll = opts.poll
  useEffect(() => {
    if (!path || !poll || IS_STATIC) return
    const t = setInterval(() => {
      const e = cache.get(key)
      if (e && e.status === 'loading') return
      // keep the old data visible while refetching: swap the entry only when the answer lands
      api(path, opts.private ? { headers: PRIVATE } : {}).then(
        (data) => { settle(null); cache.set(key, { status: 'ok', data, error: null, promise: Promise.resolve(data) }); emit() },
        (error) => settle(error),
      )
    }, poll)
    return () => clearInterval(t)
  }, [key, poll]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!path) return { data: null, error: null, loading: false, reload }
  return {
    data: entry.status === 'ok' ? entry.data : entry.data ?? null,
    error: entry.status === 'error' ? entry.error : null,
    loading: entry.status === 'loading' || entry.status === 'none',
    reload,
  }
}

/* ------------------------------------------------------------------ pure helpers */
export const short = (m) => (String(m ?? '').includes('/') ? String(m).split('/').slice(1).join('/') : String(m ?? ''))
/** 6-hex suffix of a harnesslab run id (20260909-033945-c95aff -> c95aff); other ids unchanged. */
export const sfx = (id) => { const m = /^\d{8}-\d{6}-([0-9a-f]{6})$/.exec(String(id)); return m ? m[1] : String(id) }
const nb = (v) => (v == null ? null : !!v)

/** true / false / null for one run under one suite. null is UNKNOWN, never a failure. */
export function verdict(run, oracle = 'hidden') {
  if (!run) return null
  return oracle === 'visible' ? run.vis : oracle === 'strengthened' ? run.str : run.hid
}
/** Counts under a suite: p pass, f fail, u unknown, known = p+f, rate = p/known (null if none). */
export function tally(list, oracle = 'hidden') {
  let p = 0, f = 0, u = 0
  for (const r of list || []) { const v = verdict(r, oracle); if (v === true) p++; else if (v === false) f++; else u++ }
  return { p, f, u, n: p + f + u, known: p + f, rate: p + f ? p / (p + f) : null }
}
/** Mean total tokens over runs that RECORDED usage; rec/n is the coverage to show beside it. */
export function tokStats(list) {
  const rec = (list || []).filter((r) => r.in != null && r.out != null)
  return { n: (list || []).length, rec: rec.length, mean: rec.length ? rec.reduce((a, r) => a + r.in + r.out, 0) / rec.length : null }
}
export const isMockRow = (row) => !!row && (row.models || []).length > 0 && row.models.every((m) => String(m).startsWith('mock'))
export const datasetKind = (row) => (isMockRow(row) ? 'mock' : 'recorded')
export function resolveModel(row, m) {
  if (!row || !m) return null
  return row.models.find((x) => x === m || short(x) === m) || null
}
/** The condition a dataset opens on: first model alphabetically, baseline if it exists. */
export function defaultCondition(row) {
  if (!row) return { model: null, harness: null }
  const ms = [...row.models].sort()
  return { model: ms[0] || null, harness: row.harnesses.includes('baseline') ? 'baseline' : row.harnesses[0] || null }
}
/** Resolve possibly-short / possibly-invalid model+harness against a dataset row. */
export function resolveCondition(row, model, harness) {
  if (!row) return null
  const d = defaultCondition(row)
  return { dir: row.name, model: resolveModel(row, model) || d.model, harness: row.harnesses.includes(harness) ? harness : d.harness }
}
export const conditionRuns = (runs, model, harness) => (runs || []).filter((r) => r.model === model && r.harness === harness)
/** Failure modes are every outcomes mode except passed/ungraded. */
export const isFailureMode = (mode) => !!mode && mode !== 'passed' && mode !== 'ungraded'
export const metricsCell = (metrics, model, harness) => ((metrics && metrics.cells) || []).find((c) => c.model === model && c.harness === harness) || null

export const fmt = {
  pct: (x, d = 1) => (x == null || Number.isNaN(x) ? '—' : (x * 100).toFixed(d) + '%'),
  pp: (x, d = 1) => {
    if (x == null || Number.isNaN(x)) return '—'
    const v = +(x * 100).toFixed(d)
    return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d) + 'pp'
  },
  int: (n) => (n == null || Number.isNaN(n) ? '—' : Math.round(n).toLocaleString('en-US')),
  fx: (n, d = 2) => (n == null || Number.isNaN(n) ? '—' : (+n).toFixed(d)),
  usd: (x, d = 4) => (x == null || !Number.isFinite(+x) ? '—' : '$' + (+x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })),
  plural: (n, w, p) => `${n} ${n === 1 ? w : p || w + 's'}`,
}

/* ------------------------------------------------------------------ normalisers (memoised) */
const memo = new WeakMap()
function once(obj, tag, fn) {
  if (!obj || typeof obj !== 'object') return fn()
  let m = memo.get(obj); if (!m) memo.set(obj, (m = {}))
  if (!(tag in m)) m[tag] = fn()
  return m[tag]
}

export function normOverview(raw) {
  return once(raw, 'ov', () => {
    const results = ((raw && raw.results) || []).map((r) => ({
      name: r.name, runs: r.runs, harnesses: r.harnesses || [], models: r.models || [], tasks: r.tasks || [],
      pass_rate: r.pass_rate ?? null, updated: r.updated || '', kind: datasetKind(r),
    }))
    return { results, harnesses: (raw && raw.harnesses) || [], tasks: (raw && raw.tasks) || [], key_present: !!(raw && raw.key_present), jobs: (raw && raw.jobs) || [] }
  })
}

export function normRun(r, ds) {
  // Imported third-party trajectories (no started_at, wall_ms 0) carry no cost ledger: the
  // importer writes 0.0 as a placeholder, so cost and wall time are absent, not $0.00.
  const imported = !r.started_at && !r.wall_ms
  return {
    id: r.run_id, ds, task: r.task_id, harness: r.harness_id, model: r.model, rep: r.repeat_index, exit: r.exit_reason,
    vis: nb(r.visible_pass), hid: nb(r.hidden_pass), str: nb(r.strong_pass),
    in: r.input_tokens ?? null, out: r.output_tokens ?? null,
    cost: imported ? null : (r.cost_usd ?? null), wall: imported ? null : (r.wall_ms ?? null),
    steps: r.steps, tools: r.tool_calls, edits: r.edits, bnd: r.boundary_events, bkinds: r.boundary_kinds || [],
    tests: r.tests_run_by_agent, rtbs: nb(r.ran_tests_before_submit), started: r.started_at || '', error: r.error || '',
    mode: null, cutoffCalls: null, lastFinish: null, hasLedger: null, raw: r,
  }
}
export function normRuns(raw, ds) { return once(raw, 'runs:' + ds, () => (Array.isArray(raw) ? raw : []).map((r) => normRun(r, ds))) }
export function joinOutcomes(runs, outcomes) {
  if (!outcomes || !outcomes.runs) return runs
  return once(outcomes, 'join', () => runs.map((r) => {
    const o = outcomes.runs[r.id]
    return o ? { ...r, mode: o.mode ?? null, cutoffCalls: o.cutoff_calls ?? null, lastFinish: o.last_finish ?? null, hasLedger: o.ledger ?? null } : r
  }))
}

/* ------------------------------------------------------------------ paths (one place) */
const E = encodeURIComponent
export const paths = {
  overview: () => '/overview',
  runs: (dir) => `/results/${E(dir)}/runs`,
  runDetail: (dir, id) => `/results/${E(dir)}/runs/${E(id)}`,
  outcomes: (dir) => `/outcomes/${E(dir)}`,
  metrics: (dir, baseline) => `/results/${E(dir)}/metrics${baseline ? '?baseline=' + E(baseline) : ''}`,
  comparisons: (dir, baseline = 'baseline') => `/results/${E(dir)}/comparisons?baseline=${E(baseline)}`,
  experiment: (dir) => `/results/${E(dir)}/experiment`,
  oracle: (dir) => `/results/${E(dir)}/oracle`,
  integrity: (dir, harness) => `/results/${E(dir)}/integrity${harness ? '?harness=' + E(harness) : ''}`,
  report: (dir, harness) => `/results/${E(dir)}/report${harness ? '?harness=' + E(harness) : ''}`,
  patterns: (dir, harness) => `/results/${E(dir)}/patterns${harness ? '?harness=' + E(harness) : ''}`,
  repeats: (dir, harness) => `/repeats/${E(dir)}${harness ? '?harness=' + E(harness) : ''}`,
  runRepeats: (dir, id) => `/repeats/${E(dir)}/run/${E(id)}`,
  harnessVersions: (dir) => `/harness/versions?dir=${E(dir)}`,
  sentinel: () => '/sentinel', tasks: () => '/tasks', models: () => '/models', settings: () => '/settings',
  status: () => '/status', jobs: () => '/jobs', judge: () => '/judge',
}

/* ------------------------------------------------------------------ hooks */
export function useOverview() {
  const r = useApi(paths.overview())
  return { ...r, data: r.data ? normOverview(r.data) : null }
}
export function useDatasets() {
  const r = useOverview()
  return { ...r, data: r.data ? r.data.results : null }
}
/** One dataset row; `missing` is true once the overview has loaded without it. */
export function useDataset(dir) {
  const r = useOverview()
  const row = r.data && dir ? r.data.results.find((x) => x.name === dir) || null : null
  return { ...r, data: row, missing: !!(r.data && dir && !row) }
}
export function useOutcomes(dir) {
  const r = useApi(dir ? paths.outcomes(dir) : null)
  const data = useMemo(() => {
    if (!r.data) return null
    const meaning = Object.fromEntries((r.data.modes || []).map((m) => [m.id, m]))
    const failures = Object.entries(r.data.counts || {}).filter(([k]) => isFailureMode(k)).reduce((a, [, v]) => a + v, 0)
    return { ...r.data, byId: meaning, failures }
  }, [r.data])
  return { ...r, data }
}
/** Runs of a dataset, normalised and joined with /api/outcomes. The outcomes join is
 *  best-effort: if that endpoint fails, runs still load with mode null (`outcomesError` set). */
export function useRuns(dir) {
  const r = useApi(dir ? paths.runs(dir) : null)
  const o = useApi(dir ? paths.outcomes(dir) : null)
  const data = useMemo(() => (r.data ? joinOutcomes(normRuns(r.data, dir), o.data) : null), [r.data, o.data, dir])
  return { data, error: r.error, loading: r.loading || (o.loading && !o.error), outcomesError: o.error, reload: () => { r.reload(); o.reload() } }
}
export function useRunDetail(dir, id) { return useApi(dir && id ? paths.runDetail(dir, id) : null) }
export function useMetrics(dir, baseline) { return useApi(dir ? paths.metrics(dir, baseline) : null) }
export function useComparisons(dir, baseline = 'baseline') { return useApi(dir ? paths.comparisons(dir, baseline) : null) }
export function useExperiment(dir) { return useApi(dir ? paths.experiment(dir) : null) }
export function useOracle(dir) { return useApi(dir ? paths.oracle(dir) : null) }
export function useIntegrity(dir, harness) { return useApi(dir ? paths.integrity(dir, harness) : null) }
export function useReport(dir, harness) { return useApi(dir ? paths.report(dir, harness) : null) }
export function usePatterns(dir, harness) { return useApi(dir ? paths.patterns(dir, harness) : null) }
export function useRepeats(dir, harness) { return useApi(dir ? paths.repeats(dir, harness) : null) }
export function useHarnessVersions(dir) { return useApi(dir ? paths.harnessVersions(dir) : null) }
export function useSentinel() { return useApi(paths.sentinel()) }
export function useTasks() { return useApi(paths.tasks()) }
export function useModels() { return useApi(paths.models()) }
export function useSettings() { return useApi(IS_STATIC ? null : paths.settings()) }
export function useStatus() { return useApi(paths.status()) }
/** Jobs, polled every 5 s on the live app. `running` = run ids executing right now. */
export function useJobs(poll = 5000) {
  const r = useApi(IS_STATIC ? null : paths.jobs(), { poll })
  const data = useMemo(() => {
    const jobs = Array.isArray(r.data) ? r.data : []
    const active = jobs.filter((j) => j.status === 'running' || j.status === 'queued')
    return { jobs, active, running: active.reduce((a, j) => a + ((j.running || []).length), 0) }
  }, [r.data])
  return { ...r, data }
}

/** Every dataset's runs (for the palette and run lookup). Loads lazily when `enabled`.
 *  Returns { data: Run[], loading, index: { byId, bySfx } } — never null. */
export function useAllRuns(enabled = true) {
  const ov = useOverview()
  const names = enabled && ov.data ? ov.data.results.map((x) => x.name) : []
  const namesKey = names.join('|')
  const snap = useSyncExternalStore(subscribe, () => snapshotRuns(namesKey), () => snapshotRuns(''))
  useEffect(() => { for (const n of namesKey ? namesKey.split('|') : []) if (!cache.get(paths.runs(n))) load(paths.runs(n)) }, [namesKey])
  return enabled && !ov.data ? { ...snap, loading: true } : snap
}
const allRunsSnaps = new Map()
function snapshotRuns(namesKey) {
  const names = namesKey ? namesKey.split('|') : []
  const parts = names.map((n) => cache.get(paths.runs(n)))
  const key = names.map((n, i) => n + ':' + (parts[i] ? parts[i].status : '-')).join('|')
  const hit = allRunsSnaps.get(namesKey)
  if (hit && hit.key === key && hit.parts.every((p, i) => p === parts[i])) return hit.value
  const runs = []
  let loading = false
  for (let i = 0; i < names.length; i++) {
    const e = parts[i]
    if (!e || e.status === 'loading') loading = true
    else if (e.status === 'ok') runs.push(...normRuns(e.data, names[i]))
  }
  const value = { data: runs, loading, index: indexRuns(runs) }
  allRunsSnaps.set(namesKey, { key, parts, value })
  return value
}
function indexRuns(runs) {
  const byId = new Map(), bySfx = new Map(), dup = new Set()
  for (const r of runs) {
    byId.set(r.id, r)
    const s = sfx(r.id)
    if (s !== r.id) { if (bySfx.has(s)) dup.add(s); else bySfx.set(s, r) }
  }
  for (const s of dup) bySfx.delete(s)
  return { byId, bySfx }
}
/** Shortest unambiguous handle for a run: its 6-char suffix when unique, else the full id. */
export function runHandle(run, index) {
  if (!run) return ''
  const s = sfx(run.id)
  if (!index) return s
  return index.bySfx.get(s) === run ? s : run.id
}

/** Synchronous lookup of a run by full id or unique suffix among run indexes ALREADY cached
 *  (no fetch). For title()/ctx(); views should use useFindRun, which loads what is missing. */
export function findRunCached(key) {
  const ov = peek(paths.overview())
  if (!ov || !key) return null
  const snap = snapshotRuns(normOverview(ov).results.map((r) => r.name).join('|'))
  return snap.index.byId.get(key) || snap.index.bySfx.get(key) || null
}

/** Resolve `run:<key>` (full id or unique 6-char suffix) across every dataset.
 *  data: { dir, run } once found; null while searching or when absent (`missing` then true). */
export function useFindRun(key) {
  const snap = useAllRuns(!!key)
  const ov = useOverview()
  if (!key) return { data: null, loading: false, error: null, missing: false }
  if (ov.error) return { data: null, loading: false, error: ov.error, missing: false }
  const hit = snap.index.byId.get(key) || snap.index.bySfx.get(key) || null
  if (hit) return { data: { dir: hit.ds, run: hit }, loading: false, error: null, missing: false }
  const loading = !ov.data || snap.loading
  return { data: null, loading, error: null, missing: !loading }
}
