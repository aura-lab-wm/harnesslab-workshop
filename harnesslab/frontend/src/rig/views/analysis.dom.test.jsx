// @vitest-environment jsdom
/* views/analysis.jsx — the eight analysis steps and the trajectory explorer on the hand-countable
   `mini` fixture (testing.jsx). Extra endpoints are spread onto rigFixture(), never edited in it. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture, MINI } from '../../../test/rig-testing'

afterEach(resetRig)

const M1 = 'acme/alpha-1', M2 = 'acme/beta-2'
function data() {
  const f = rigFixture()
  const metrics = f[`/results/${MINI}/metrics`]
  metrics.cells[0] = {
    ...metrics.cells[0], 'pass^3': 0.5, 'pass@3': 1, pass1_strong: 0.5,
    tasks: { t1: { n: 2, c: 2, pass1: 1, outcomes: [true, true] }, t2: { n: 2, c: 1, pass1: 0.5, outcomes: [true, false] } },
    passk_curve: [{ k: 1, pass_at_k: 0.75, pass_pow_k: 0.75 }, { k: 2, pass_at_k: 1, pass_pow_k: 0.5 }],
  }
  const exp = f[`/results/${MINI}/experiment`]
  f[`/results/${MINI}/experiment`] = {
    ...exp, outcome: 'hidden_pass', a_levels: [M1, M2], b_levels: ['baseline', 'terse'],
    cells: [{ a: M1, b: 'baseline', n: 4, pass1: 0.75, ci95: [0.5, 1] }, { a: M1, b: 'terse', n: 4, pass1: 1, ci95: [1, 1] },
      { a: M2, b: 'baseline', n: 3, pass1: 1 / 3, ci95: [0, 0.5] }, { a: M2, b: 'terse', n: 4, pass1: 0.75, ci95: [0.5, 1] }],
    anova: { n: 15, ss_total: 3.2, dropped: [], table: [
      { term: 'model (A)', SS: 0.64, df: 1, MS: 0.64, F: 4.1, share: 0.2 }, { term: 'harness (B)', SS: 0.16, df: 1, MS: 0.16, F: 1.02, share: 0.05 },
      { term: 'residual (repeats)', SS: 2.4, df: 12, MS: 0.2, F: null, share: 0.75 }] },
  }
  f[`/results/${MINI}/comparisons?baseline=baseline`] = { baseline: 'baseline', comparisons: {
    [M1]: { terse: { n_tasks: 2, mean_diff: 0.25, ci95: [-0.1, 0.5], per_task: { t1: 0, t2: 0.5 } } },
    [M2]: { terse: { n_tasks: 2, mean_diff: 0.5, ci95: [0.25, 0.75], per_task: { t1: 0, t2: 1 } } } } }
  const integ = f[`/results/${MINI}/integrity?harness=baseline`]
  integ.leakage = integ.leakage.map((x) => ({ ...x, pass1_strong: x.task === 't1' ? 0.5 : x.pass1, steps: 5, tokens: 1200 }))
  integ.weak_tests = { per_task: [{ task: 't1', lost: 0.4 }, { task: 't2', lost: 0 }] }
  integ.self_report = [{ harness: 'baseline', overclaim: 0.1 }, { harness: 'terse', overclaim: 0 }]
  f['/judge'] = { status: 'idle', stage: null, error: null, report: null, history: [] }
  f[`/results/${MINI}/report?harness=baseline`] = { ...f[`/results/${MINI}/report`], markdown: '# Evaluation report card\n' }
  f['/models'] = { models: [{ id: M1, price_in: 0.1, price_out: 0.4 }, { id: 'mock', price_in: null, price_out: null }], observed: { [M1]: { cost_per_run: 0.01, runs: 8 } } }
  return f
}
const el = (c, id) => c.querySelector(`[data-el~="${id}"]`)
const mine = (requests) => requests.filter((r) => /\/(results|outcomes|jobs|judge)/.test(r.url))

describe('an: shell', () => {
  it('every analysis tab shows the Asking sentence and the step nav', () => {
    const { container } = renderRig('#/rig/an:mini:fit', { data: data() })
    expect(el(container, 'asking-sentence').textContent).toContain('Asking')
    const nav = el(container, 'analysis-nav')
    expect(within(nav).getAllByRole('button').map((b) => b.textContent.replace(/\d$/, ''))).toEqual(
      ['Family matrix', 'Outcomes', 'Comparison', 'Trajectories', 'Judge & integrity', 'Experiment', 'Report', 'Run setup'])
    act(() => { fireEvent.click(within(nav).getByText('Judge & integrity')) })
    expect(location.hash).toBe('#/rig/an:mini:judge:alpha-1:baseline')     // replaces the tab in place
  })
  it('an unknown view or dataset is a not-found document, not a crash', () => {
    const a = renderRig('#/rig/an:mini:nope', { data: data() })
    expect(el(a.container, 'not-found').textContent).toContain('not an analysis view')
    resetRig()
    const b = renderRig('#/rig/an:nowhere:family', { data: data() })
    expect(el(b.container, 'not-found')).toBeTruthy()
  })
  it('a condition change in the Asking sentence retargets the analysis tab', () => {
    renderRig('#/rig/an:mini:outcomes', { data: data() })
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: M2 } })
    expect(location.hash).toBe('#/rig/an:mini:outcomes:beta-2:baseline?c=mini:acme/beta-2:baseline')
  })
})

describe('an: family matrix', () => {
  it('counts every family from the runs, keeps unknowns apart, and a cell opens its trajectories', () => {
    const { container, requests } = renderRig('#/rig/an:mini:family', { data: data() })
    const mx = el(container, 'family-matrix')
    const rows = mx.querySelectorAll('tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('3 / 4 known')                 // alpha-1 · baseline: ✓✓ ✓×
    expect(rows[0].textContent).not.toContain('unknown')                 // a zero-unknown cell says nothing about it
    expect(rows[1].textContent).toContain('1 / 3 known · 1 unknown')     // beta-2 · baseline: ✓? ××
    expect(rows[1].textContent).toContain('33.3%')
    expect(el(container, 'grade-basis').textContent).toContain('hidden-suite grades')
    expect(el(container, 'repeat-counts-differ')).toBeNull()            // every family has 4 runs
    act(() => { fireEvent.click(within(rows[1]).getAllByTitle(/Open the trajectories of beta-2 under terse/)[0]) })
    expect(location.hash).toBe('#/rig/an:mini:family+!tasks:mini:beta-2:terse')
    expect(mine(requests)).toEqual([])
  })
  it('the model filter narrows the rows; a cell pin goes to the case file', () => {
    const { container } = renderRig('#/rig/an:mini:family', { data: data() })
    fireEvent.change(screen.getByLabelText('Model filter'), { target: { value: M2 } })
    expect(el(container, 'family-matrix').querySelectorAll('tbody tr')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Pin beta-2 · baseline · mini' }))
    expect(JSON.parse(localStorage.getItem('rig.case')).map((x) => x.id)).toContain(`cell:mini:${M2}:baseline`)
  })
})

describe('an: outcomes', () => {
  it('headline, pass^k, flip rate and strengthened delta come from the metrics cell', () => {
    const { container } = renderRig('#/rig/an:mini:outcomes:alpha-1:baseline', { data: data() })
    expect(el(container, 'outcomes-headline').textContent).toContain('75.0')
    expect(el(container, 'outcomes-headline').textContent).toContain('95% CI [50.0%, 100.0%]')
    expect(el(container, 'pass-k').textContent).toContain('50.0%')
    expect(el(container, 'flip-rate').textContent).toContain('1 of 2')
    expect(el(container, 'strengthened-delta').textContent).toContain('−25.0pp')
  })
  it('per-task strips mark failures with their mode and never count an unknown as a failure', () => {
    const a = renderRig('#/rig/an:mini:outcomes:alpha-1:baseline', { data: data() })
    const p = el(a.container, 'per-task-repeats')
    expect(p.textContent).toContain('cut off · no patch')
    expect(p.textContent).toContain('×1')
    expect(p.querySelector('.rg-amb').textContent).toContain('◆')                // t2 carries a probe
    resetRig()
    const b = renderRig('#/rig/an:mini:outcomes:beta-2:baseline', { data: data() })
    const q = el(b.container, 'per-task-repeats')
    expect(q.querySelectorAll('.rg-an-c.u')).toHaveLength(1)
    expect(q.textContent).toContain('1/1 · 1?')
    expect(q.textContent).toContain('wrong patch')
    act(() => { fireEvent.click(q.querySelector('.rg-an-c.u')) })
    expect(location.hash).toContain('task:mini:beta-2:baseline:t1:1')
  })
})

describe('an: comparison', () => {
  it('Δ-matrix counts separating contrasts; clicking a cell shows per-task Δ and opens its runs in the split', () => {
    const { container } = renderRig('#/rig/an:mini:delta', { data: data() })
    const d = el(container, 'delta-matrix')
    expect(d.textContent).toContain('1 of 2')
    expect(d.textContent).toContain('◆ separates')
    expect(d.textContent).toContain('○ covers 0')
    act(() => { fireEvent.click(screen.getByRole('button', { name: /beta-2 terse: delta \+50.0pp, separates/ })) })
    expect(location.hash).toBe('#/rig/an:mini:delta|tasks:mini:beta-2:terse?f=1')
    expect(el(container, 'delta-detail').textContent).toContain('+100pp')
    expect(el(container, 'variance-decomposition').textContent).toContain('residual (repeats)')
  })
})

describe('an: judge & integrity', () => {
  it('oracle 2×2, κ, flags, and the LLM judge waits for a key', () => {
    const { container, requests } = renderRig('#/rig/an:mini:judge', { data: data() })
    const o = el(container, 'oracle-comparison')
    expect(o.textContent).toContain('0.87')
    expect(o.textContent).toContain('−6.7pp')
    expect(within(o).getAllByRole('row')[1].textContent).toContain('101')          // hidden ✓: 10 both, 1 only-hidden
    const flags = el(container, 'integrity-flags').textContent
    expect(flags).toContain('contamination probe')
    expect(flags).toContain('weak hidden tests')
    expect(flags).toContain('over-claiming harness')
    expect(el(container, 'leakage-evidence').textContent).toContain('solution leak')
    const j = el(container, 'llm-judge')
    expect(j.textContent).toContain('not set')
    expect(within(j).queryByRole('button', { name: 'Run the judge' })).toBeNull()        // no dead button without a key
    expect(within(j).getByRole('button', { name: "Set the lab's server key in Settings" })).toBeTruthy()
    expect(requests.filter((r) => r.method !== 'GET')).toEqual([])
  })
})

describe('an: experiment', () => {
  it('ANOVA table, cell grid and contrast filter', () => {
    const { container } = renderRig('#/rig/an:mini:fit', { data: data() })
    expect(el(container, 'two-factor-fit').textContent).toContain('model (A)')
    expect(el(container, 'two-factor-fit').textContent).toContain('30.0pp')
    expect(el(container, 'cell-grid-ci').textContent).toContain('n=3')
    const pc = el(container, 'pairwise-contrasts')
    expect(pc.querySelectorAll('tbody tr')).toHaveLength(2)
    fireEvent.click(within(pc).getByRole('button', { name: 'separating 1' }))
    expect(pc.querySelectorAll('tbody tr')).toHaveLength(1)
  })
})

describe('an: report card', () => {
  it('labels a pooled card honestly and states the pooled n', () => {
    const { container } = renderRig('#/rig/an:mini:report', { data: data() })
    expect(el(container, 'report-cell-label').textContent).toBe('all 2 models · baseline (pooled) · 8 runs')
    expect(el(container, 'report-pooled-note').textContent).toContain('alpha-1 alone has 4')
    expect(el(container, 'report-card').textContent).toContain('71.4%')
    expect(screen.getByRole('button', { name: 'Export .md' })).toBeTruthy()
  })
})

describe('an: run setup', () => {
  it('estimates the cell and launches only after an explicit confirm, on POST /jobs', async () => {
    const { container, requests } = renderRig('#/rig/an:mini:setup', { data: data() })
    const s = el(container, 'run-setup')
    expect(el(container, 'run-estimate').textContent).toContain('6 = 1×1×2×3')
    expect(el(container, 'run-estimate').textContent).toContain('≈ $0.06')
    fireEvent.click(within(s).getByRole('button', { name: 'Launch 6 runs' }))
    expect(requests.filter((r) => r.method === 'POST')).toEqual([])              // armed, nothing sent
    await act(async () => { fireEvent.click(within(s).getByRole('button', { name: 'Start 6 runs' })) })
    expect(requests.filter((r) => r.method === 'POST')).toEqual([{ url: '/api/jobs', method: 'POST' }])
    expect(el(container, 'error-state').textContent).toContain('not primed')     // the stub 404 is shown, not swallowed
  })
})

describe('tasks: trajectory explorer', () => {
  it('one row per task with ✓/×/? ribbons, counts and failure modes', () => {
    const { container, requests } = renderRig('#/rig/tasks:mini:beta-2:baseline', { data: data() })
    const t = el(container, 'task-outcome-table')
    const rows = t.querySelectorAll('.rg-an-trow:not(.th)')
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain('t2')                              // most failures first
    expect(rows[0].textContent).toContain('wrong patch')
    expect(rows[0].textContent).toContain('no patch')
    expect(rows[1].querySelectorAll('.rg-an-c.u')).toHaveLength(1)
    expect(el(container, 'asking-sentence')).toBeTruthy()
    expect(mine(requests)).toEqual([])
  })
  it('quick filters, failure-mode filter, search and sort', () => {
    const { container } = renderRig('#/rig/tasks:mini:alpha-1:baseline', { data: data() })
    const rows = () => [...el(container, 'task-outcome-table').querySelectorAll('.rg-an-trow:not(.th) b')].map((b) => b.textContent)
    expect(rows()).toEqual(['t2', 't1'])
    fireEvent.click(screen.getByRole('button', { name: 'Mixed outcomes 1' }))
    expect(rows()).toEqual(['t2'])
    fireEvent.click(screen.getByRole('button', { name: 'All 2' }))
    fireEvent.change(screen.getByLabelText('Sort tasks'), { target: { value: 'name' } })
    expect(rows()).toEqual(['t1', 't2'])
    fireEvent.change(screen.getByLabelText('Failure mode filter'), { target: { value: 'cutoff_no_patch' } })
    expect(rows()).toEqual(['t2'])
    fireEvent.change(screen.getByLabelText('Failure mode filter'), { target: { value: 'all' } })
    fireEvent.change(screen.getByPlaceholderText('Search tasks'), { target: { value: 'zzz' } })
    expect(screen.getByText('No task matches these filters.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(rows()).toEqual(['t1', 't2'])
  })
  it('a ribbon cell opens that attempt in the task investigator', () => {
    const { container } = renderRig('#/rig/tasks:mini:alpha-1:baseline', { data: data() })
    act(() => { fireEvent.click(el(container, 'task-outcome-table').querySelector('.rg-an-c.f')) })
    expect(location.hash).toContain('task:mini:alpha-1:baseline:t2:1')
  })
})

/* ------------------------------------------------------------------ round 3: dead ends by dataset shape
   solo  2 models × ONE harness (swe), no strengthened grades anywhere (like real_swe_agent_500)
   duo   ONE model × 2 harnesses, no `baseline` harness (like demo_mock)
   wide  2 models × 7 harnesses (paged family matrix); mock-x only ran under h1
   many  1 model × 45 tasks (paged explorer) */
const BIG = 'acme/big', SMALL = 'acme/small'
let seq = 0
function raw(model, harness, task, rep, hid, str = null) {
  return { run_id: `20260102-000000-${(0xb00000 + seq++).toString(16)}`, task_id: task, harness_id: harness, model, repeat_index: rep, started_at: '2026-01-02T00:00:00Z',
    exit_reason: hid === false ? 'no_action' : 'submitted', steps: 3, tool_calls: 3, edits: 1, boundary_events: 0, boundary_kinds: [], tests_run_by_agent: 0,
    input_tokens: 100, output_tokens: 10, cost_usd: 0, wall_ms: 1000, visible_pass: hid, hidden_pass: hid, strong_pass: str, error: '' }
}
const noModes = () => ({ runs: {}, counts: {}, modes: [] })   // one object per dataset: data.js memoises the join on it
function shapes({ key = false, judge = { status: 'idle', stage: null, error: null, report: null, history: [] } } = {}) {
  const f = data()
  const ov = f['/overview']
  const solo = [raw(BIG, 'swe', 's1', 0, true), raw(BIG, 'swe', 's1', 1, false), raw(BIG, 'swe', 's2', 0, false), raw(BIG, 'swe', 's2', 1, false), raw(SMALL, 'swe', 's1', 0, true), raw(SMALL, 'swe', 's2', 0, false)]
  const duo = [raw('mock', 'permissive', 'd1', 0, false), raw('mock', 'permissive+sentinel', 'd1', 0, true)]
  const H7 = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'h7']
  const wide = [...H7.map((h) => raw('mock-w', h, 'w1', 0, true)), raw('mock-x', 'h1', 'w1', 0, false)]
  const many = Array.from({ length: 45 }, (_, i) => raw('mock-m', 'baseline', `task${String(i).padStart(2, '0')}`, 0, i % 3 !== 0))
  f['/overview'] = { ...ov, key_present: key, results: [...ov.results,
    { name: 'solo', runs: 6, harnesses: ['swe'], models: [BIG, SMALL], tasks: ['s1', 's2'], pass_rate: 1 / 3, updated: '' },
    { name: 'duo', runs: 2, harnesses: ['permissive', 'permissive+sentinel'], models: ['mock'], tasks: ['d1'], pass_rate: 0.5, updated: '' },
    { name: 'wide', runs: 8, harnesses: H7, models: ['mock-w', 'mock-x'], tasks: ['w1'], pass_rate: 7 / 8, updated: '' },
    { name: 'many', runs: 45, harnesses: ['baseline'], models: ['mock-m'], tasks: many.map((r) => r.task_id), pass_rate: 0.67, updated: '' }] }
  Object.assign(f, {
    '/judge': judge,
    '/results/solo/runs': solo, '/outcomes/solo': noModes(),
    '/results/solo/metrics': { cells: [
      { model: BIG, harness: 'swe', runs: 4, repeats: 2, 'pass@1': 0.25, ci95: [0, 0.5], flip_rate: 0.5, pass1_strong: 0, tasks: { s1: { n: 2, c: 1 }, s2: { n: 2, c: 0 } } },
      { model: SMALL, harness: 'swe', runs: 2, repeats: 1, 'pass@1': 0.5, ci95: [0, 1], flip_rate: 0, pass1_strong: 0, tasks: { s1: { n: 1, c: 1 }, s2: { n: 1, c: 0 } } }], comparison: {} },
    '/results/solo/comparisons?baseline=baseline': { baseline: 'swe', comparisons: { [BIG]: {}, [SMALL]: {} } },
    '/results/solo/experiment': { outcome: 'hidden_pass', a_levels: [BIG, SMALL], b_levels: ['swe'], anova: null, contrasts: [], multiplicity: { n: 0, expected_false: 0 },
      cells: [{ a: BIG, b: 'swe', n: 4, pass1: 0.25, ci95: [0, 0.5] }, { a: SMALL, b: 'swe', n: 2, pass1: 0.5, ci95: [0, 1] }],
      fit: { status: 'insufficient_data', reason: 'fewer_than_2_balanced_levels_b' } },
    '/results/solo/oracle': { n_eligible: 0, n_excluded: 6, cells: { both_true: 0, both_false: 0, only_a_true: 0, only_b_true: 0 }, rate_a: { rate: null, n: 0 }, rate_b: { rate: null, n: 0 }, kappa: null, kappa_undefined_reason: 'no_eligible_pairs' },
    '/results/solo/integrity?harness=swe': { harness: 'swe', leakage: [{ task: 's1', probe: null, pass1: 2 / 3, pass1_strong: 0, patch_issue_similarity: null }, { task: 's2', probe: null, pass1: 0, pass1_strong: 0, patch_issue_similarity: null }],
      weak_tests: { per_task: [{ task: 's1', n_passed: 2, lost: 1 }] }, self_report: [{ harness: 'swe', overclaim: 0 }] },
    '/results/solo/report?harness=swe': { card: { cell: { model: BIG, harness: { id: 'swe' }, runs: 6, tasks: ['s1', 's2'], oracle: 'hidden unittest suite' },
      outcome: { pass1: 1 / 3, ci95: [0, 0.67], pass_at_k: {}, pass_pow_k: {}, pass1_strong: 0, flip_rate: 0.5 }, missing: ['Two toy tasks.'] } },
    '/results/duo/comparisons?baseline=baseline': { baseline: 'permissive', comparisons: { mock: { 'permissive+sentinel': { n_tasks: 1, mean_diff: 1, ci95: [0, 1], per_task: { d1: 1 } } } } },
    '/results/duo/experiment': { outcome: 'hidden_pass', a_levels: ['mock'], b_levels: ['permissive', 'permissive+sentinel'], anova: null, multiplicity: { n: 1, expected_false: 0.05 },
      cells: [{ a: 'mock', b: 'permissive', n: 1, pass1: 0, ci95: [0, 0] }, { a: 'mock', b: 'permissive+sentinel', n: 1, pass1: 1, ci95: [1, 1] }],
      contrasts: [{ a: 'mock', from: 'permissive', to: 'permissive+sentinel', delta: 1, ci95: [0, 1], tasks: 1, covers_zero: true }],
      fit: { status: 'insufficient_data', reason: 'fewer_than_2_balanced_levels_a' } },
    '/results/wide/runs': wide, '/outcomes/wide': noModes(),
    '/results/many/runs': many, '/outcomes/many': noModes(),
  })
  return f
}
/** DEAD-END RULE 1: every disabled button names its reason in visible text beside it. */
function everyDisabledSaysWhy(container) {
  for (const b of container.querySelectorAll('.rig button:disabled')) {
    const d = (b.getAttribute('aria-describedby') || '').split(/\s+/).map((id) => document.getElementById(id)).find(Boolean)
    expect(d && d.textContent.trim().length > 3, `disabled “${b.textContent}” says why`).toBe(true)
  }
}
/** DEAD-END RULE 2: every empty state offers at least one way forward. */
function everyEmptyHasAWay(container) {
  const empties = container.querySelectorAll('[data-el~="empty-state"]')
  for (const e of empties) expect(e.querySelectorAll('button:not(:disabled)').length, `“${e.textContent.slice(0, 60)}” offers a way forward`).toBeGreaterThan(0)
  return empties.length
}

describe('round 3 · dead ends: one harness, no strengthened grades (solo)', () => {
  it('comparison says what the dataset has and offers the views that DO answer', () => {
    const { container } = renderRig('#/rig/an:solo:delta', { data: shapes() })
    const e = el(el(container, 'delta-matrix'), 'empty-state')
    expect(e.textContent).toContain('One harness, so nothing to compare against.')
    expect(e.textContent).toContain('solo has 2 models under a single harness (swe)')
    expect(el(container, 'variance-decomposition')).toBeNull()                   // one message, not two
    expect(everyEmptyHasAWay(container)).toBe(1)
    act(() => { fireEvent.click(within(e).getByRole('button', { name: 'Compare its 2 models in the family matrix' })) })
    expect(location.hash).toBe('#/rig/an:solo:delta+!an:solo:family:big:swe')
  })
  it('the other dataset offered is one that can answer (several harnesses, recorded first)', () => {
    const { container } = renderRig('#/rig/an:solo:delta', { data: shapes() })
    act(() => { fireEvent.click(within(el(container, 'delta-matrix')).getByRole('button', { name: /Open the comparison in mini \(2 models × 2 harnesses\)/ })) })
    expect(location.hash).toBe('#/rig/an:solo:delta+!an:mini:delta')
  })
  it('experiment: no fit is explained by shape, the cell grid still answers, no empty contrasts panel', () => {
    const { container } = renderRig('#/rig/an:solo:fit', { data: shapes() })
    const e = el(el(container, 'two-factor-fit'), 'empty-state')
    expect(e.textContent).toContain('fewer than two balanced harnesses')
    expect(within(e).getByRole('button', { name: 'Compare its 2 models in the family matrix' })).toBeTruthy()
    expect(within(e).getByRole('button', { name: /Open the fit for mini/ })).toBeTruthy()
    expect(el(container, 'cell-grid-ci').textContent).toContain('n=4')
    expect(el(container, 'pairwise-contrasts')).toBeNull()
    everyEmptyHasAWay(container)
  })
  it('report: no two-factor claims offers ways forward; Export .md says why it is disabled; pooled label kept', () => {
    const { container } = renderRig('#/rig/an:solo:report', { data: shapes() })
    const claims = el(container, 'report-claims')
    expect(claims.textContent).toContain('No two-factor claims.')
    everyEmptyHasAWay(container)
    const md = screen.getByRole('button', { name: 'Export .md' })
    expect(md.disabled).toBe(true)
    everyDisabledSaysWhy(container)
    expect(el(container, 'report-cell-label').textContent).toBe('all 2 models · swe (pooled) · 6 runs')
    expect(el(container, 'report-card').textContent).toContain('not graded')         // strengthened 0.0 from the backend is not a grade
    expect(el(container, 'report-card').textContent).toContain('no strengthened grades')
  })
  it('judge: no oracle pair → names the shape, links a dataset that has one, and drops the invented weak-test flag', () => {
    const { container, requests } = renderRig('#/rig/an:solo:judge', { data: shapes() })
    const o = el(container, 'oracle-comparison')
    expect(o.textContent).toContain('Only one suite graded these runs.')
    expect(o.textContent).toContain('mini has 15 runs graded by both')
    act(() => { fireEvent.click(within(o).getByRole('button', { name: 'Open the oracle comparison in mini' })) })
    expect(location.hash).toBe('#/rig/an:solo:judge+!an:mini:judge')
    resetRig()
    const b = renderRig('#/rig/an:solo:judge', { data: shapes() })
    expect(el(b.container, 'integrity-flags').textContent).toContain('0 open')
    expect(el(b.container, 'integrity-flags').textContent).toContain('weak-test check is skipped')
    expect(el(b.container, 'per-task-oracle').textContent).toContain('not graded')
    expect(el(b.container, 'per-task-oracle').textContent).not.toContain('−67pp')
    everyEmptyHasAWay(b.container)
    everyDisabledSaysWhy(b.container)
    expect(requests.filter((r) => r.method !== 'GET')).toEqual([])
  })
  it('outcomes: the strengthened figure is "—", not 0%, when no run was strengthened-graded; uneven repeats are stated', () => {
    const { container } = renderRig('#/rig/an:solo:outcomes:small:swe', { data: shapes() })
    const s = el(container, 'strengthened-delta')
    expect(s.textContent).toContain('—')
    expect(s.textContent).toContain('not graded')
    expect(s.textContent).not.toContain('0.0%')
    resetRig()
    const b = renderRig('#/rig/an:solo:outcomes:big:swe', { data: shapes() })
    expect(el(b.container, 'outcomes-answer').textContent).toContain('pass@1 for big under swe is 25.0%, 95% CI [0.0%, 50.0%]')
    expect(el(b.container, 'outcomes-headline').textContent).toContain('2 repeats')
  })
})

describe('round 3 · dead ends: one model, no baseline harness (duo)', () => {
  it('comparison has its one contrast; the empty variance split points to what can answer', () => {
    const { container } = renderRig('#/rig/an:duo:delta', { data: shapes() })
    expect(el(container, 'delta-count').textContent).toBe('0 of 1 separate')
    const v = el(container, 'variance-decomposition')
    expect(v.textContent).toContain('duo has one model (mock) under 2 harnesses, so there is no model factor')
    expect(within(v).getByRole('button', { name: 'Cell grid with intervals' })).toBeTruthy()
    expect(within(v).getByRole('button', { name: 'Open the variance split in mini' })).toBeTruthy()
    everyEmptyHasAWay(container)
  })
  it('experiment: offers the harness comparison it can make instead', () => {
    const { container } = renderRig('#/rig/an:duo:fit', { data: shapes() })
    const e = el(el(container, 'two-factor-fit'), 'empty-state')
    expect(el(container, 'pairwise-contrasts')).toBeTruthy()                   // its one contrast still shows
    act(() => { fireEvent.click(within(e).getByRole('button', { name: 'Compare its 2 harnesses in Comparison' })) })
    expect(location.hash).toBe('#/rig/an:duo:fit+!an:duo:delta:mock:permissive')
  })
})

describe('round 3 · dead ends: paging never shows a disabled arrow', () => {
  it('a family matrix that fits says so and shows no arrows', () => {
    const { container } = renderRig('#/rig/an:mini:family', { data: shapes() })
    expect(el(container, 'family-pages').textContent).toBe('all 2 harnesses shown')
    expect(screen.queryByRole('button', { name: /harnesses/ })).toBeNull()
  })
  it('a paged family matrix hides the arrow at each end', () => {
    const { container } = renderRig('#/rig/an:wide:family', { data: shapes() })
    const pg = () => el(container, 'family-pages')
    expect(pg().textContent).toContain('harnesses 1–6 of 7')
    expect(within(pg()).queryByRole('button', { name: '← Previous harnesses' })).toBeNull()
    fireEvent.click(within(pg()).getByRole('button', { name: 'More harnesses →' }))
    expect(pg().textContent).toContain('harnesses 7–7 of 7')
    expect(within(pg()).queryByRole('button', { name: 'More harnesses →' })).toBeNull()
    expect(within(pg()).getByRole('button', { name: '← Previous harnesses' })).toBeTruthy()
    everyDisabledSaysWhy(container)
  })
  it('the explorer pager hides prev on the first page and next on the last', () => {
    const { container } = renderRig('#/rig/tasks:many:mock-m:baseline', { data: shapes() })
    const pg = () => el(container, 'task-pages')
    expect(pg().textContent).toContain('1–40 of 45 tasks')
    expect(within(pg()).queryByRole('button', { name: /Previous/ })).toBeNull()
    fireEvent.click(within(pg()).getByRole('button', { name: 'Next 5 →' }))
    expect(pg().textContent).toContain('41–45 of 45 tasks')
    expect(within(pg()).queryByRole('button', { name: /Next/ })).toBeNull()
    expect(within(pg()).getByRole('button', { name: '← Previous 40' })).toBeTruthy()
  })
  it('an explorer tab on a family that was never run offers the matrix, not text alone', () => {
    const { container } = renderRig('#/rig/tasks:wide:mock-x:h7', { data: shapes() })
    const e = el(container, 'empty-state')
    expect(e.textContent).toContain('No runs in this condition.')
    act(() => { fireEvent.click(within(e).getByRole('button', { name: 'Pick an observed family in the matrix' })) })
    expect(location.hash).toBe('#/rig/tasks:wide:mock-x:h7+!an:wide:family:mock-x:h7')
  })
})

describe('round 3 · dead ends: the LLM judge', () => {
  it('without a key: says what unlocks it (Settings) and offers the checks that need no key', () => {
    const { container } = renderRig('#/rig/an:mini:judge', { data: shapes() })
    const j = el(container, 'llm-judge')
    expect(j.textContent).toContain("needs the lab's server key, which is not set")
    expect(j.textContent).not.toContain('idle')                                // no uninformative status word
    expect(within(j).getByRole('button', { name: 'See the oracle agreement (no key needed)' })).toBeTruthy()
    everyDisabledSaysWhy(container)
    act(() => { fireEvent.click(within(j).getByRole('button', { name: "Set the lab's server key in Settings" })) })
    expect(location.hash).toBe('#/rig/an:mini:judge+!settings')
  })
  it('with a key: runs only after a confirm, and a failure offers Retry and Settings', async () => {
    const { container, requests } = renderRig('#/rig/an:mini:judge', { data: shapes({ key: true }) })
    const j = el(container, 'llm-judge')
    fireEvent.click(within(j).getByRole('button', { name: 'Run the judge' }))
    expect(requests.filter((r) => r.method === 'POST')).toEqual([])
    await act(async () => { fireEvent.click(within(j).getByRole('button', { name: 'Start the judge' })) })
    expect(requests.filter((r) => r.method === 'POST')).toEqual([{ url: '/api/judge/run', method: 'POST' }])
    const err = el(container, 'judge-error')
    expect(err.textContent).toContain('not primed')
    expect(within(err).getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
  it('while a judge run is in progress the disabled button says why', () => {
    const { container } = renderRig('#/rig/an:mini:judge', { data: shapes({ key: true, judge: { status: 'running', report: null, history: [] } }) })
    const b = within(el(container, 'llm-judge')).getByRole('button', { name: 'Judge running…' })
    expect(b.disabled).toBe(true)
    expect(document.getElementById(b.getAttribute('aria-describedby')).textContent).toContain('A judge run is in progress')
  })
})

describe('round 3 · dead ends: run setup', () => {
  it('Launch disabled with nothing selected says what enables it', () => {
    const { container } = renderRig('#/rig/an:mini:setup', { data: data() })
    fireEvent.click(within(el(container, 'run-setup')).getByRole('checkbox', { name: /alpha-1/ }))
    const b = screen.getByRole('button', { name: 'Launch 0 runs' })
    expect(b.disabled).toBe(true)
    expect(document.getElementById(b.getAttribute('aria-describedby')).textContent).toBe('Select a model to launch.')
    everyDisabledSaysWhy(container)
  })
  it('a launched run ends in a next step: watch it on the canvas', async () => {
    const { container } = renderRig('#/rig/an:mini:setup', { data: data() })
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => ({ id: 'job-7' }) })))
    fireEvent.click(screen.getByRole('button', { name: 'Launch 6 runs' }))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start 6 runs' })) })
    const r = el(container, 'launch-result')
    expect(r.textContent).toContain('Launched job-7: 6 runs → data/runs/mini.')
    act(() => { fireEvent.click(within(r).getByRole('button', { name: 'Watch the run on the canvas' })) })
    expect(location.hash).toBe('#/rig/an:mini:setup+!canvas')
  })
})

describe('round 3 · breathing: every view leads with its answer and keeps method text behind About', () => {
  it.each([['family', 'family-answer'], ['outcomes', 'outcomes-answer'], ['delta', 'delta-answer'], ['judge', 'judge-answer'], ['fit', 'fit-answer'], ['setup', 'setup-answer']])('%s', (v, id) => {
    const { container } = renderRig(`#/rig/an:mini:${v}`, { data: data() })
    const lead = el(container, id)
    expect(lead.tagName).toBe('P')
    expect(lead.textContent.length).toBeGreaterThan(20)
    const about = container.querySelector('.rg-an-head details')
    expect(about.open).toBe(false)
    expect(about.querySelector('summary').textContent).toBe('About this view')
  })
  it('the family answer names the highest and lowest family with denominators', () => {
    const { container } = renderRig('#/rig/an:mini:family', { data: data() })
    expect(el(container, 'family-answer').textContent).toBe('Highest: alpha-1 under terse at 100.0% (4/4); lowest: beta-2 under baseline at 33.3% (1/3) — hidden-suite grades among known ones, over 4 families.')
  })
})
