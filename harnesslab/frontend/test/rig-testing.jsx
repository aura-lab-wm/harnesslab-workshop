/* ====================================================================================
   Rig · testing.jsx — helpers for dom tests of Rig views (NOT imported by app code).

     import { renderRig, rigFixture, MINI } from '../../test/rig-testing'
     const { container, requests } = renderRig('#/rig/ds:mini', { data: rigFixture() })

   renderRig(hash, { data, fetch }) primes the data cache with `data` (path -> response, the
   same paths data.js requests, e.g. '/overview', '/results/mini/runs'), stubs fetch so any
   UNPRIMED path answers 404 (recorded in `requests`, so a test can assert nothing unexpected
   was asked for), stubs EventSource, sets location.hash and renders <RigApp/>.
   Call resetRig() in afterEach (it also runs cleanup()).

   rigFixture() is a tiny, fully consistent dataset "mini" (2 models × 2 harnesses × 2 tasks ×
   2 repeats = 16 runs, one unknown grade, outcomes modes, metrics, experiment, oracle,
   integrity, sentinel, report) plus a mock control "mock_ctl". Every count in it is derivable
   by hand; see the comments.
   ==================================================================================== */
import { cleanup, render } from '@testing-library/react'
import { vi } from 'vitest'
import RigApp from '../src/rig/RigApp'
import { prime, resetCache } from '../src/rig/data'
import { resetTabState } from '../src/rig/context'
import { reloadCase } from '../src/rig/caseFile'

export const MINI = 'mini'
const M1 = 'acme/alpha-1', M2 = 'acme/beta-2'

function run(i, model, harness, task, rep, vis, hid, str, extra = {}) {
  return {
    run_id: `20260101-000000-${(0xa00000 + i).toString(16)}`,   // …-a00000, …-a00001 (6-hex suffix)
    task_id: task, harness_id: harness, model, repeat_index: rep, started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z',
    exit_reason: hid === false ? 'no_action' : 'submitted', steps: 4 + (i % 3), tool_calls: 6, edits: hid === false ? 0 : 1, boundary_events: 0, boundary_kinds: [],
    tests_run_by_agent: 1, ran_tests_before_submit: true, input_tokens: 1000 + i * 10, output_tokens: 100, cost_usd: 0.001, wall_ms: 5000,
    visible_pass: vis, hidden_pass: hid, strong_pass: str, error: '', ...extra,
  }
}

/** 16 runs. alpha-1/baseline: t1 ✓✓, t2 ✓× (mixed, the × is cut off). alpha-1/terse: t1 ✓✓, t2 ✓✓.
 *  beta-2/baseline: t1 ✓?(unknown hidden), t2 ××(wrong patch, no patch). beta-2/terse: t1 ✓✓, t2 ×✓(step limit).
 *  hidden: 11 pass, 4 fail, 1 unknown. One hidden pass fails strengthened (alpha-1/baseline t1 rep 1). */
export function miniRuns() {
  const R = []
  let i = 0
  const add = (m, h, t, rep, vis, hid, str, extra) => R.push(run(i++, m, h, t, rep, vis, hid, str, extra))
  add(M1, 'baseline', 't1', 0, true, true, true); add(M1, 'baseline', 't1', 1, true, true, false)
  add(M1, 'baseline', 't2', 0, true, true, true); add(M1, 'baseline', 't2', 1, false, false, false, { input_tokens: null, output_tokens: null })
  add(M1, 'terse', 't1', 0, true, true, true); add(M1, 'terse', 't1', 1, true, true, true)
  add(M1, 'terse', 't2', 0, true, true, true); add(M1, 'terse', 't2', 1, true, true, true)
  add(M2, 'baseline', 't1', 0, true, true, true); add(M2, 'baseline', 't1', 1, true, null, null)
  add(M2, 'baseline', 't2', 0, true, false, false); add(M2, 'baseline', 't2', 1, false, false, false)
  add(M2, 'terse', 't1', 0, true, true, true); add(M2, 'terse', 't1', 1, true, true, true)
  add(M2, 'terse', 't2', 0, false, false, false); add(M2, 'terse', 't2', 1, true, true, true)
  return R
}
const MODES = [
  { id: 'passed', label: 'passed', meaning: 'The hidden suite passed.' },
  { id: 'ungraded', label: 'ungraded', meaning: 'No hidden grade was recorded. Unknown, not a failure.' },
  { id: 'harness_error', label: 'harness error', meaning: "The run recorded an error; the failure may not be the agent's." },
  { id: 'cutoff_no_patch', label: 'cut off · no patch', meaning: 'The last model reply hit the per-call output limit with no tool call.' },
  { id: 'step_limit_no_patch', label: 'step limit · no patch', meaning: 'The run used all its steps without producing a patch.' },
  { id: 'no_patch', label: 'no patch', meaning: 'The run ended without producing a patch.' },
  { id: 'wrong_patch', label: 'wrong patch', meaning: 'The agent produced a patch and the hidden suite failed on it.' },
]
export function miniOutcomes(runs = miniRuns()) {
  const failMode = { 3: 'cutoff_no_patch', 10: 'wrong_patch', 11: 'no_patch', 14: 'step_limit_no_patch' }
  const out = {}
  runs.forEach((r, i) => { out[r.run_id] = { mode: r.hidden_pass === true ? 'passed' : r.hidden_pass == null ? 'ungraded' : failMode[i], cutoff_calls: failMode[i] === 'cutoff_no_patch' ? 1 : 0, last_finish: failMode[i] === 'cutoff_no_patch' ? 'length' : 'tool_calls', ledger: true } })
  const counts = Object.fromEntries(MODES.map((m) => [m.id, 0]))
  for (const o of Object.values(out)) counts[o.mode]++
  return { runs: out, counts, modes: MODES }
}

export function rigFixture() {
  const runs = miniRuns()
  const mockRuns = [run(90, 'mock', 'baseline', 't1', 0, true, true, true), run(91, 'mock', 'baseline', 't1', 1, true, false, false)]
  return {
    '/overview': {
      results: [
        { name: MINI, runs: 16, harnesses: ['baseline', 'terse'], models: [M1, M2], tasks: ['t1', 't2'], pass_rate: 11 / 15, updated: '2026-01-02T00:00:00Z' },
        { name: 'mock_ctl', runs: 2, harnesses: ['baseline'], models: ['mock'], tasks: ['t1'], pass_rate: 0.5, updated: '2026-01-01T00:00:00Z' },
      ],
      harnesses: [{ id: 'baseline' }, { id: 'terse' }], tasks: [{ id: 't1', title: 'first task', probe: 'none' }, { id: 't2', title: 'second task', probe: 'solution_leak' }], jobs: [], key_present: false,
    },
    '/jobs': [],
    '/tasks': [{ id: 't1', title: 'first task', probe: 'none' }, { id: 't2', title: 'second task', probe: 'solution_leak' }],
    [`/results/${MINI}/runs`]: runs,
    [`/outcomes/${MINI}`]: miniOutcomes(runs),
    '/results/mock_ctl/runs': mockRuns,
    '/outcomes/mock_ctl': { runs: { [mockRuns[0].run_id]: { mode: 'passed', ledger: true }, [mockRuns[1].run_id]: { mode: 'wrong_patch', ledger: true } }, counts: { passed: 1, wrong_patch: 1 }, modes: MODES },
    [`/results/${MINI}/metrics`]: { cells: [
      { model: M1, harness: 'baseline', runs: 4, repeats: 2, 'pass@1': 0.75, ci95: [0.5, 1], flip_rate: 0.5 },
      { model: M1, harness: 'terse', runs: 4, repeats: 2, 'pass@1': 1, ci95: [1, 1], flip_rate: 0 },
      { model: M2, harness: 'baseline', runs: 4, repeats: 2, 'pass@1': 1 / 3, ci95: [0, 0.5], flip_rate: 0 },
      { model: M2, harness: 'terse', runs: 4, repeats: 2, 'pass@1': 0.75, ci95: [0.5, 1], flip_rate: 0.5 },
    ], comparison: {} },
    [`/results/${MINI}/experiment`]: {
      fit: { leading_factor: 'model', model: { share: 0.2, range: 0.3 }, harness: { share: 0.05, range: 0.1 }, interaction_share: 0.01 },
      contrasts: [{ a: M2, from: 'baseline', to: 'terse', delta: 0.5, ci95: [0.25, 0.75], tasks: 2, covers_zero: false },
        { a: M1, from: 'baseline', to: 'terse', delta: 0.25, ci95: [-0.1, 0.5], tasks: 2, covers_zero: true }],
      multiplicity: { n: 2, expected_false: 0.1, bonferroni_pct: 97.5 },
    },
    [`/results/${MINI}/oracle`]: { n_eligible: 15, n_excluded: 1, cells: { both_true: 10, both_false: 4, only_a_true: 1, only_b_true: 0 }, rate_a: { rate: 11 / 15, n: 15 }, rate_b: { rate: 10 / 15, n: 15 }, kappa: 0.87, kappa_undefined_reason: null },
    [`/results/${MINI}/integrity?harness=baseline`]: { harness: 'baseline', leakage: [
      { task: 't1', probe: 'none', pass1: 0.83, patch_issue_similarity: 0.05 }, { task: 't2', probe: 'solution_leak', pass1: 0.5, patch_issue_similarity: 0.4 }] },
    '/sentinel': { active: 'mini_model', default_config: { threshold: 0.6 }, models: [
      { name: 'mini_model', active: true, meta: { sources: [MINI], n_runs: 16, auc_run_weighted: 0.7, operating_points: [{ threshold: 0.5, recall: 0.75, false_alarm: 0.3, mean_lead_steps: 2 }, { threshold: 0.6, recall: 0.5, false_alarm: 0.1, mean_lead_steps: 1.5 }] } }] },
    [`/results/${MINI}/report`]: { card: { cell: { model: M1, harness: { id: 'baseline' }, runs: 8, repeats: 2, tasks: ['t1', 't2'] },
      outcome: { pass1: 5 / 7, ci95: [0.4, 1], pass_at_k: { 2: 0.9 }, pass_pow_k: { 2: 0.5, 3: 0.4 }, pass1_strong: 4 / 7 }, missing: ['Two toy tasks.'] } },
  }
}

/** Render the Rig at `hash` with primed data. Unprimed GETs 404 and are recorded. */
export function renderRig(hash = '#/rig/', { data = rigFixture(), storage, Root = RigApp } = {}) {
  resetCache(); resetTabState()
  if (storage) for (const [k, v] of Object.entries(storage)) localStorage.setItem(k, v)
  reloadCase()
  prime(data)
  const requests = []
  vi.stubGlobal('EventSource', vi.fn(function () { this.close = vi.fn() }))
  vi.stubGlobal('fetch', vi.fn(async (url, opts) => {
    requests.push({ url: String(url), method: (opts && opts.method) || 'GET' })
    return { ok: false, status: 404, statusText: 'Not Found', headers: { get: () => 'application/json' }, json: async () => ({ detail: `not primed in the test: ${url}` }) }
  }))
  history.replaceState(null, '', hash)
  const utils = render(<Root />)
  return { ...utils, requests }
}
export function resetRig() {
  cleanup(); resetCache(); resetTabState()
  try { localStorage.clear() } catch { /* ignore */ }
  reloadCase()
  vi.unstubAllGlobals()
}
