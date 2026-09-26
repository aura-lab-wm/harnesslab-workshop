// @vitest-environment jsdom
/* views/workspace.jsx — sources, capture (private), sentinel, canvas, settings, guide, package and
   the capture dock. Every write is tested only as far as its confirm step, then cancelled; where a
   test does confirm, fetch is a local stub and the test asserts the exact endpoint and body. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture, MINI } from '../../../test/rig-testing'
import { invalidate } from '../data'
import { sessionFolders, canvasLayout } from './workspace'

afterEach(resetRig)

const M1 = 'acme/alpha-1', M2 = 'acme/beta-2'
const el = (c, id) => c.querySelector(`[data-el~="${id}"]`)
/** The visible reason a disabled control carries (aria-describedby → <Why>). */
const whyOf = (b) => { const id = b.getAttribute('aria-describedby'); const d = id && document.getElementById(id); return d ? d.textContent : null }

/** Replace fetch with a router: routes(url, opts) -> body (200) | undefined (404). Records calls. */
function stubFetch(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, opts = {}) => {
    const u = String(url)
    calls.push({ url: u, method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body ? JSON.parse(opts.body) : undefined })
    const hit = routes(u, opts)
    if (hit === undefined) return { ok: false, status: 404, statusText: 'Not Found', headers: { get: () => 'application/json' }, json: async () => ({ detail: 'not here' }) }
    return { ok: true, status: 200, headers: { get: () => 'application/json' }, json: async () => hit }
  }))
  return calls
}

const IMPORT = {
  '/import/sources': { sources: [{ name: 'codex', description: 'OpenAI Codex CLI rollout JSONL (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)', patterns: ['rollout-*.jsonl'] }, { name: 'inspect', description: 'Inspect AI eval log (.eval zip or .json)', patterns: ['*.eval'] }] },
  '/import/status': { status: 'idle', progress: null, error: null, result: null, history: [{ source: 'codex', results_dir: 'imported_codex_old', imported: 3, skipped: 0 }] },
  '/real/status': { status: 'idle', progress: null, error: null, result: null },
  [`/harness/versions?dir=${MINI}`]: { dir: MINI, versions: [{ harness_id: 'baseline', hash: 'aaa111', runs: 8, pass1: 0.75, matches_current: true, has_file: true }, { harness_id: 'terse', hash: 'bbb222', runs: 8, pass1: 0.875, matches_current: false, has_file: true }] },
  '/settings': { key_present: true, key_hint: 'sk-or-v…abcd', base_url: 'https://openrouter.ai/api/v1', lab_root: '/lab', results_root: '/lab/data/runs', harness_dir: '/lab/harnesses', task_dir: '/lab/tasks', results: [MINI, 'mock_ctl'], harnesses: ['baseline', 'terse'] },
}

describe('sources', () => {
  it('lists results on disk with descriptions, runs, models, updated, and the harness versions', () => {
    const { container, requests } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    const disk = el(container, 'results-on-disk')
    const rows = within(disk).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.querySelector('b').textContent)).toEqual(['mini', 'mock_ctl'])
    expect(rows[0].textContent).toContain('16')
    expect(rows[0].textContent).toContain('2 models')
    expect(rows[1].textContent).toContain('No description ships with this directory.')
    expect(container.textContent).toContain('/lab/data/runs · 18 runs in 2 directories')
    expect(container.textContent).toContain('× drifted')
    expect(requests).toEqual([])
  })
  it('"status: idle · 0 past imports" is gone; earlier imports are listed, each with a way to open it', () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    expect(container.textContent).not.toMatch(/status: idle|past imports/)
    const h = el(container, 'import-history')
    expect(h.textContent).toContain('imported_codex_old')
    expect(h.textContent).toContain('3 imported')
    expect(within(h).getByRole('button', { name: 'Open imported_codex_old' }).getAttribute('data-spec')).toBe('ds:imported_codex_old')
  })
  it('the path placeholder reads as an example; the adapters\' session folders are one-click chips', async () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    const calls = stubFetch((u) => (u.startsWith('/api/import/detect') ? { exists: false, path: '/home/u/.codex/sessions/', is_dir: false, source: '', candidates: [], sessions: null, error: 'no such file or directory' } : undefined))
    const box = el(container, 'import-trace')
    const input = within(box).getByLabelText(/Path to a session file/)
    expect(input.getAttribute('placeholder')).toMatch(/^e\.g\. /)
    const chip = within(el(box, 'path-suggestions')).getByRole('button', { name: /Codex/ })
    expect(chip.textContent).toContain('~/.codex/sessions/')
    fireEvent.click(chip)
    expect(input.value).toBe('~/.codex/sessions/')
    await waitFor(() => expect(el(box, 'import-detect').textContent).toContain('Nothing at /home/u/.codex/sessions/'))
    expect(calls.some((c) => c.url === '/api/import/detect?path=' + encodeURIComponent('~/.codex/sessions/'))).toBe(true)
    const imp = within(box).getByRole('button', { name: 'Import this path' })
    expect(imp.disabled).toBe(true)
    expect(whyOf(imp)).toBe('Nothing at this path — check it, or pick a folder above')
    expect(calls.filter((c) => c.method === 'POST')).toEqual([])
  })
  it('session folders are read from the adapter descriptions, up to the first templated segment', () => {
    const live = [
      { name: 'claude_code', description: 'Claude Code session JSONL (~/.claude/projects/<project>/<session>.jsonl)' },
      { name: 'codex', description: 'OpenAI Codex CLI rollout JSONL (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)' },
      { name: 'cursor', description: 'Cursor agent transcripts (~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl)' },
      { name: 'gemini_cli', description: 'Gemini CLI session JSONL (~/.gemini/tmp/<project>/chats/*.jsonl)' },
      { name: 'inspect', description: 'Inspect AI eval log (.eval zip or .json); each sample becomes one run' },
    ]
    expect(sessionFolders(live)).toEqual([
      { source: 'claude_code', path: '~/.claude/projects/' }, { source: 'codex', path: '~/.codex/sessions/' },
      { source: 'cursor', path: '~/.cursor/projects/' }, { source: 'gemini_cli', path: '~/.gemini/tmp/' },
    ])
  })
  it('disabled Inspect and Import say why; an unrecognised path asks for an adapter, then Import opens', async () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    stubFetch((u) => (u.startsWith('/api/import/detect') ? { exists: true, path: '/tmp/y.json', is_dir: false, source: '', candidates: [{ source: 'inspect', confidence: 0.2, files: 1 }], sessions: null, error: '' } : undefined))
    const box = el(container, 'import-trace')
    const inspect = within(box).getByRole('button', { name: 'Inspect' })
    const imp = () => within(box).getByRole('button', { name: 'Import this path' })
    expect(inspect.disabled).toBe(true)
    expect(whyOf(inspect)).toBe('Enter a path first')
    expect(imp().disabled).toBe(true)
    expect(whyOf(imp())).toBe('Enter a path, then Inspect it')
    fireEvent.change(within(box).getByLabelText(/Path to a session file/), { target: { value: '/tmp/y.json' } })
    await waitFor(() => expect(el(box, 'import-detect').textContent).toContain('No adapter recognises this path.'))
    expect(whyOf(imp())).toBe('No adapter recognises this path — choose one under Adapter')
    fireEvent.change(within(box).getByLabelText('Adapter'), { target: { value: 'inspect' } })
    expect(imp().disabled).toBe(false)
    expect(el(box, 'import-detect').textContent).toContain('You chose Inspect, so Import reads it that way.')
    expect(within(box).getByDisplayValue('imported_inspect_y_json')).toBeTruthy()
  })
  it('an adapter that cannot read the path on this server blocks Import, says why, and offers the fix it names', async () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    stubFetch((u) => (u.startsWith('/api/import/detect') ? { exists: true, path: '/tmp/a.eval', is_dir: false, source: 'inspect', candidates: [{ source: 'inspect', confidence: 0.95, files: 1 }], sessions: null, error: 'RuntimeError: Inspect logs need the inspect_ai package: pip install inspect_ai' } : undefined))
    const box = el(container, 'import-trace')
    fireEvent.change(within(box).getByLabelText(/Path to a session file/), { target: { value: '/tmp/a.eval' } })
    await waitFor(() => expect(el(box, 'import-detect').textContent).toContain('Recognised as Inspect format (confidence 0.95).'))
    expect(el(box, 'import-detect').textContent).toContain('could not read it on this server: RuntimeError: Inspect logs need the inspect_ai package')
    expect(el(box, 'import-fix').textContent).toContain('pip install inspect_ai')
    const imp = within(box).getByRole('button', { name: 'Import this path' })
    expect(imp.disabled).toBe(true)
    expect(whyOf(imp)).toBe('The inspect adapter cannot read this here — see above')
  })
  it('the two roads have distinct labels; there is no bare "Import" button', () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    const add = el(container, 'add-runs')
    expect(within(add).getByRole('button', { name: 'Import this path' })).toBeTruthy()
    expect(within(add).getByRole('button', { name: 'Download SWE-agent sample' })).toBeTruthy()
    expect(within(add).queryByRole('button', { name: 'Import' })).toBeNull()
    expect(within(el(container, 'road-capture')).getByRole('button', { name: 'Open Session capture' }).getAttribute('data-spec')).toBe('capture')
    expect(el(container, 'import-about').tagName).toBe('DETAILS')          // the explanation sits behind a disclosure
  })
  it('a finished import ends in "Open <dataset>" and "Import another path"', () => {
    const result = { source: 'codex', results_dir: 'imported_codex_x', imported: 3, skipped: 0, n_tasks: 2, tasks: ['t1'], outcomes_known: 3, outcomes_unknown: 0, harnesses: [], errors: [] }
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT, '/import/status': { status: 'done', result, history: [result] } } })
    const r = el(container, 'import-result')
    expect(r.textContent).toContain('Last import · Codex → data/runs/imported_codex_x')
    expect(within(r).getByRole('button', { name: 'Open imported_codex_x' }).getAttribute('data-spec')).toBe('ds:imported_codex_x')
    expect(within(r).getByRole('button', { name: 'Import another path' })).toBeTruthy()
    expect(el(container, 'import-history')).toBeNull()                      // the latest one is not repeated as "earlier"
  })
  it('a live session store is steered into the private directory and says so', async () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    stubFetch((u) => (u.startsWith('/api/import/detect') ? { exists: true, path: '/home/u/.codex/sessions/', is_dir: true, source: 'codex', candidates: [{ source: 'codex', confidence: 0.95, files: 4 }], sessions: 4, error: '' } : undefined))
    const box = el(container, 'import-trace')
    fireEvent.click(within(el(box, 'path-suggestions')).getByRole('button', { name: /Codex/ }))
    await waitFor(() => expect(el(box, 'import-detect').textContent).toContain('Found 4 sessions in Codex format (confidence 0.95).'))
    expect(within(box).getByDisplayValue('captured')).toBeTruthy()
    expect(el(box, 'import-detect').textContent).toContain('Codex keeps its live sessions here, so they stay private')
    fireEvent.click(within(box).getByRole('button', { name: 'Import this path' }))
    expect(box.textContent).toContain('Writes data/runs/captured from ~/.codex/sessions/')   // the confirm names what it writes
    fireEvent.click(within(box).getByRole('button', { name: 'Cancel' }))
  })
  it('the SWE-agent download names the directory it writes and offers a free name when that one exists', () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    const box = el(container, 'swe-agent-import')
    expect(box.textContent).toContain('Writes data/runs/real_swe_agent_500')
    fireEvent.change(within(box).getByLabelText(/Results directory/), { target: { value: 'mini' } })
    expect(el(box, 'swe-dir-taken').textContent).toContain('data/runs/mini already exists')
    fireEvent.click(within(box).getByRole('button', { name: 'Use mini_2' }))
    expect(within(box).getByLabelText(/Results directory/).value).toBe('mini_2')
    for (const i of box.querySelectorAll('input')) if (i.placeholder) expect(i.placeholder).toMatch(/^e\.g\. /)
  })
  it('import: detect a path, arm the confirm, cancel (no POST), then confirm posts to /api/import', async () => {
    const { container } = renderRig('#/rig/sources', { data: { ...rigFixture(), ...IMPORT } })
    const calls = stubFetch((u) => (u.startsWith('/api/import/detect') ? { exists: true, path: '/tmp/x', source: 'codex', sessions: 3, candidates: [{ source: 'codex', confidence: 0.9 }], is_dir: true } : u === '/api/import' ? { ok: true } : u === '/api/import/status' ? IMPORT['/import/status'] : undefined))
    const box = el(container, 'import-trace')
    const importBtn = () => within(box).getByRole('button', { name: 'Import this path' })
    expect(importBtn().disabled).toBe(true)
    fireEvent.change(within(box).getByLabelText(/Path to a session file/), { target: { value: '/tmp/x' } })
    await waitFor(() => expect(within(box).getByRole('button', { name: 'Inspect' }).disabled).toBe(false))
    fireEvent.click(within(box).getByRole('button', { name: 'Inspect' }))
    await waitFor(() => expect(box.textContent).toContain('Found 3 sessions in Codex format (confidence 0.90).'))
    expect(el(box, 'import-detect').textContent).toContain('writes one run per session to data/runs/imported_codex_x, a new directory.')
    expect(within(box).getByDisplayValue('imported_codex_x')).toBeTruthy()
    fireEvent.click(importBtn())
    expect(box.textContent).toContain('Creates data/runs/imported_codex_x from /tmp/x.')
    fireEvent.click(within(box).getByRole('button', { name: 'Cancel' }))
    expect(calls.filter((c) => c.method === 'POST')).toEqual([])
    fireEvent.click(importBtn())
    await act(async () => { fireEvent.click(within(box).getByRole('button', { name: 'Import now' })) })
    const post = calls.find((c) => c.method === 'POST')
    expect(post.url).toBe('/api/import')
    expect(post.body).toEqual({ path: '/tmp/x', results_dir: 'imported_codex_x', source: null })
  })
})

const CAPTURE = {
  runs_indexed: 12, open_runs: 1, open: [{ run_id: 'cap-open-1', task_id: 'fix the parser', steps: 7, last_ts: '2026-09-01T10:00:00Z' }],
  totals: { input_tokens: 120000, output_tokens: 8000, cost_usd: 1.5, unmeasured_runs: 2 },
  adapters: ['claude_code', 'codex'], sniffer: { state: 'idle', roots: 2, interval_s: 60, errors: 0, debris: 0, debris_checked_at: 0 },
  cursors: { sources: 2, seeded: 0 }, control: { paused: false },
}
const CAPTURE_NONE = {
  runs_indexed: 0, open_runs: 0, open: [], totals: { input_tokens: 0, output_tokens: 0, cost_usd: 0, unmeasured_runs: 0 },
  adapters: ['claude_code', 'codex', 'qwen_code', 'gemini_cli', 'cursor'], sniffer: { state: 'never_started', roots: 0, interval_s: 60, errors: 0, debris: 0, debris_checked_at: 0 },
  cursors: { sources: 0, seeded: 0 }, control: { paused: false },
}
const capNone = () => ({ ...rigFixture(), 'private:/capture/status': CAPTURE_NONE, 'private:/capture/runs?limit=25': { rows: [], total: 0 }, 'private:/capture/relations': { relations: {} } })
const capData = () => ({ ...rigFixture(), 'private:/capture/status': CAPTURE, 'private:/capture/runs?limit=25': { rows: [{ run_id: 'cap-1', task_id: 'rename a flag', model: 'anthropic/claude-x', steps: 9, cost_usd: 0.12, exit_reason: 'submitted' }], total: 12 }, 'private:/capture/relations': { relations: { 'cap-0': { superseded_by: ['cap-1'] } } } })

describe('capture', () => {
  it('shows the private notice, KPIs with unmeasured runs named, watcher, open, relations, recent runs', () => {
    const { container, requests } = renderRig('#/rig/capture', { data: capData() })
    const cap = el(container, 'capture')
    expect(el(cap, 'capture-private').textContent).toMatch(/Private\..*excluded from every export/)
    const k = el(cap, 'capture-kpis').textContent
    expect(k).toContain('12'); expect(k).toContain('120,000'); expect(k).toContain('$1.50'); expect(k).toContain('2 runs recorded no usage')
    expect(cap.textContent).toContain('Watching 2 roots, every 60s')
    expect(cap.textContent).toContain('fix the parser')
    expect(cap.textContent).toContain('1 superseded · 0 forked')
    expect(cap.textContent).toContain('rename a flag')
    expect(requests).toEqual([])
  })
  it('every capture request carries the private header', async () => {
    renderRig('#/rig/capture', { data: rigFixture() })
    const calls = stubFetch((u) => (u === '/api/capture/status' ? CAPTURE : u.startsWith('/api/capture/runs') ? { rows: [], total: 0 } : u === '/api/capture/relations' ? { relations: {} } : undefined))
    await act(async () => { invalidate('/capture') })
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith('/api/capture/')).length).toBeGreaterThanOrEqual(3))
    for (const c of calls.filter((x) => x.url.startsWith('/api/capture/'))) expect(c.headers['x-harnesslab-private']).toBe('1')
  })
  it('pause sits behind a confirm; confirming posts {action: pause} with the private header', async () => {
    const { container } = renderRig('#/rig/capture', { data: capData() })
    const calls = stubFetch((u) => (u === '/api/capture/control' ? { ok: true } : u === '/api/capture/status' ? CAPTURE : undefined))
    const w = el(container, 'capture-watcher')
    fireEvent.click(within(w).getByRole('button', { name: 'Pause capture' }))
    fireEvent.click(within(w).getByRole('button', { name: 'Cancel' }))
    expect(calls.filter((c) => c.method === 'POST')).toEqual([])
    fireEvent.click(within(w).getByRole('button', { name: 'Pause capture' }))
    await act(async () => { fireEvent.click(within(w).getByRole('button', { name: 'Pause the watcher' })) })
    const post = calls.find((c) => c.method === 'POST')
    expect(post.url).toBe('/api/capture/control')
    expect(post.body).toEqual({ action: 'pause' })
    expect(post.headers['x-harnesslab-private']).toBe('1')
  })
  it('the capture dock shows the watcher and opens the page', () => {
    const { container } = renderRig('#/rig/home?dock=capture', { data: capData() })
    const d = el(container, 'capture-dock')
    expect(d.textContent).toContain('Watching'); expect(d.textContent).toContain('12 captured'); expect(d.textContent).toContain('private')
    expect(d.textContent).not.toMatch(/never started|idle/)
    act(() => { fireEvent.click(within(d).getByRole('button', { name: /Open Session capture/ })) })
    expect(location.hash).toContain('capture')
  })
  it('nothing watching: Capture now says why, and the start and backfill commands are copyable', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const { container } = renderRig('#/rig/capture', { data: capNone() })
    const cap = el(container, 'capture')
    const now = within(cap).getByRole('button', { name: 'Capture now' })
    expect(now.disabled).toBe(true)
    expect(whyOf(now)).toBe('Nothing is watching — start a watcher first (command above)')
    expect(within(cap).queryByRole('button', { name: 'Pause capture' })).toBeNull()      // nothing to pause
    expect(el(cap, 'capture-start').textContent).toContain('python -m harnesslab.capture --watch')
    const empty = el(cap, 'capture-empty')
    expect(empty.textContent).toContain('Nothing captured yet.')
    expect(el(empty, 'capture-backfill').textContent).toContain('python -m harnesslab.capture --backfill')
    expect(within(empty).getByRole('button', { name: 'Import a session file instead' }).getAttribute('data-spec')).toBe('sources')
    await act(async () => { fireEvent.click(within(el(empty, 'capture-backfill')).getByRole('button', { name: /Copy/ })) })
    expect(writeText).toHaveBeenCalledWith('python -m harnesslab.capture --backfill')
    expect(el(cap, 'capture-kpis')).toBeNull()                                            // no row of zeros
    expect(cap.textContent).not.toMatch(/Nothing in flight|never_started/)
    delete navigator.clipboard
  })
  it('the dock, with nothing watching, gives the start command instead of a dead button', () => {
    const { container } = renderRig('#/rig/home?dock=capture', { data: capNone() })
    const d = el(container, 'capture-dock')
    expect(d.textContent).toContain('Not running')
    expect(el(d, 'capture-start').textContent).toContain('--watch')
    expect(whyOf(within(d).getByRole('button', { name: 'Capture now' }))).toMatch(/^Nothing is watching/)
  })
})

const SENT = {
  active: 'mini_model', default_config: { threshold: 0.6 },
  model: { names: ['n_edits', 'no_test_yet', 'odd_feature'], w: [-0.4, 1.2, 0.3], b: -1.1, meta: {
    trained: true, name: 'mini_model', n_runs: 16, n_examples: 60, folds: 5, fail_rate: 0.25, trained_at: '2026-01-03T00:00:00Z', harness_filter: 'baseline',
    auc_all_prefixes: 0.81, auc_run_weighted: 0.7, auc_ci: { any_prefix: [0.6, 0.9], bootstrap: 200 }, auc_baselines: { rules_only: 0.66, prior: 0.6 }, ece: 0.12,
    oracle_invisible_failure_share: 0.5, oracle_invisible: { n_fail: 4, invisible: 2 },
    auc_by_prefix: [{ bucket: '0-50%', auc: 0.6 }, { bucket: '50-100%', auc: 0.9 }],
    calibration: [{ bin: '0.0-0.5', n: 40, fail_rate: 0.1 }, { bin: '0.5-1.0', n: 20, fail_rate: 0.5 }],
    operating_points: [{ threshold: 0.5, recall: 0.75, false_alarm: 0.3, mean_lead_steps: 2 }, { threshold: 0.6, recall: 0.5, false_alarm: 0.1, mean_lead_steps: 1.5 }] } },
  models: [{ name: 'mini_model', active: true, meta: { n_runs: 16, auc_all_prefixes: 0.81, auc_run_weighted: 0.7 } }, { name: 'other', meta: { n_runs: 40, auc_all_prefixes: 0.9, auc_run_weighted: 0.8 } }],
}
const PLUG = { dir: '/lab/plugins', n_builtin: 1, n_plugin: 1, errors: [], detectors: [
  { id: 'submit_without_verify', source: 'builtin', label: 'Submitting without re-running tests', severity: 'high', why: 'Edited after the last test.', nudge: 'Run the tests.' },
  { id: 'streak', source: 'rule', file: 'streak.rule.json', label: 'Two edits since the test', severity: 'medium', why: 'Stacked edits.', when: 'edits_since_test >= 2 and steps_frac > 0.5', enabled: true }] }

describe('sentinel', () => {
  it('shows AUC, the deployed operating point, and the slider moves between evaluated points only', () => {
    const { container } = renderRig('#/rig/sentinel', { data: { ...rigFixture(), '/sentinel': SENT, '/sentinel/plugins': PLUG, '/../openapi.json': { paths: {} } } })
    const s = el(container, 'sentinel')
    expect(s.textContent).toContain('0.81'); expect(s.textContent).toContain('95% [0.60, 0.90]'); expect(s.textContent).toContain('2 of 4 fails')
    expect(s.textContent).toContain('At 0.60 the hook blocks about 10% of good submits to catch 50% of bad ones')
    fireEvent.change(screen.getByLabelText('Sentinel threshold operating point'), { target: { value: '0' } })
    expect(s.textContent).toContain('At 0.50 the hook blocks about 30% of good submits to catch 75% of bad ones')
    expect(s.textContent).toContain('has not run the tests yet')      // plain name
    expect(s.textContent).toContain('odd_feature')                     // unknown name shown raw
    expect(el(s, 'sentinel-plugins').textContent).toContain('when edits_since_test >= 2 and steps_frac > 0.5')
    const lb = within(el(s, 'sentinel-leaderboard')).getAllByRole('row')
    expect(lb[1].textContent).toContain('1. other')                    // sorted by AUC
    expect(lb[2].textContent).toContain('active')
  })
  it('with no rule-test route, testing is shown as unavailable with the terminal alternative — never a dead button', () => {
    const { container } = renderRig('#/rig/sentinel', { data: { ...rigFixture(), '/sentinel': SENT, '/sentinel/plugins': PLUG, '/../openapi.json': { paths: {} } } })
    const t = el(container, 'sentinel-test-rule')
    expect(within(t).queryByRole('button', { name: 'Test rule' })).toBeNull()
    const u = el(t, 'rule-test-unavailable')
    expect(u.textContent).toContain('Testing here is unavailable.')
    expect(u.textContent).toContain('POST /api/sentinel/plugins/test_rule')
    fireEvent.change(within(t).getByLabelText('edits_since_test'), { target: { value: '3' } })
    expect(within(t).getByLabelText('steps_frac')).toBeTruthy()
    const cmd = el(u, 'rule-test-command').textContent
    expect(cmd).toContain('safe_eval("edits_since_test >= 2 and steps_frac > 0.5"')
    expect(cmd).toContain('{"edits_since_test":3,"steps_frac":0}')
    expect(within(u).getByRole('button', { name: /Copy/ })).toBeTruthy()
  })
  it('when the route list cannot be read (no OpenAPI document), it says so instead of claiming the route is absent', () => {
    const { container } = renderRig('#/rig/sentinel', { data: { ...rigFixture(), '/sentinel': SENT, '/sentinel/plugins': PLUG, '/../openapi.json': '<!doctype html><html></html>' } })
    const u = el(el(container, 'sentinel-test-rule'), 'rule-test-unavailable')
    expect(u.textContent).toContain("This server's route list could not be read (no OpenAPI document at /openapi.json)")
    expect(u.textContent).not.toContain('registers no rule-test route')
    expect(el(u, 'rule-test-command')).toBeTruthy()
  })
  it('when the server does register the rule-test route, Test rule posts the rule and the values', async () => {
    const { container } = renderRig('#/rig/sentinel', { data: { ...rigFixture(), '/sentinel': SENT, '/sentinel/plugins': PLUG, '/../openapi.json': { paths: { '/api/sentinel/plugins/test_rule': {} } } } })
    const calls = stubFetch((u) => (u === '/api/sentinel/plugins/test_rule' ? { fired: true } : undefined))
    const t = el(container, 'sentinel-test-rule')
    expect(el(t, 'rule-test-unavailable')).toBeNull()
    fireEvent.change(within(t).getByLabelText('steps_frac'), { target: { value: '0.75' } })
    await act(async () => { fireEvent.click(within(t).getByRole('button', { name: 'Test rule' })) })
    const post = calls.find((c) => c.method === 'POST')
    expect(post.body).toEqual({ id: 'streak', when: 'edits_since_test >= 2 and steps_frac > 0.5', env: { edits_since_test: 0, steps_frac: 0.75 } })
    expect(t.textContent).toContain('"fired": true')
  })
  it('an untrained sentinel says so instead of drawing curves, and gives the command that trains one', () => {
    const { container } = renderRig('#/rig/sentinel', { data: { ...rigFixture(), '/sentinel': { models: [], model: { meta: { trained: false } } }, '/sentinel/plugins': PLUG, '/../openapi.json': { paths: {} } } })
    expect(el(container, 'sentinel').textContent).toContain('No trained model on disk')
    expect(el(container, 'sentinel-train').textContent).toContain('train(["data/runs/mini"])')
  })
})

const CMP = { baseline: 'baseline', comparisons: { [M1]: { terse: { n_tasks: 2, mean_diff: 0.25, ci95: [-0.1, 0.5] } }, [M2]: { terse: { n_tasks: 2, mean_diff: 0.5, ci95: [0.25, 0.75] } } } }
describe('canvas', () => {
  it('layout spreads the three columns over any width, never below the compact tree, and grows rows with the height', () => {
    const counts = { s: 7, m: 5, c: 6 }
    const compact = canvasLayout(0, 0, counts)
    expect(compact.NW).toBe(200)
    expect(compact.CX[1] - compact.CX[0]).toBe(260)                    // 200 node + 60 gap: the old fixed tree
    for (const w of [390, 800, 1100, 1288, 1600, 2200, 3200, 5000]) {
      const L = canvasLayout(w, 900, counts)
      expect(L.W, `${w}`).toBeGreaterThanOrEqual(Math.min(w, L.W))
      expect(L.CX[0]).toBeGreaterThanOrEqual(16)
      expect(L.CX[2] + L.NW + 150, `${w}: tree fits its width`).toBeLessThanOrEqual(L.W)
      expect(L.CX[1] - L.CX[0]).toBe(L.CX[2] - L.CX[1])                // even spacing
      if (w >= 1288) expect(L.CX[1] - L.CX[0] - L.NW, `${w}: gap grows`).toBeGreaterThan(60)
    }
    expect(canvasLayout(1288, 0, counts).CX[1]).toBeGreaterThan(compact.CX[1])
    expect(canvasLayout(5000, 900, counts).NW).toBeLessThanOrEqual(300)     // wide screens: capped node width, centred tree
    const tall = canvasLayout(1288, 1400, counts)
    expect(tall.pitch.s).toBeGreaterThan(84); expect(tall.pitch.s).toBeLessThanOrEqual(126)
  })
  it('wires study → model → cells; the detail follows the selection with paired Δ and actions', () => {
    const { container, requests } = renderRig('#/rig/canvas', { data: { ...rigFixture(), [`/results/${MINI}/comparisons?baseline=baseline`]: CMP } })
    const cv = el(container, 'canvas')
    const detail = () => el(cv, 'canvas-detail')
    expect(detail().textContent).toContain('alpha-1 × baseline')
    expect(detail().textContent).toContain('75.0%')
    expect(detail().textContent).toContain('No harness separates from baseline')
    fireEvent.click(within(cv).getByRole('button', { name: /beta-2/ }))
    expect(detail().textContent).toContain('beta-2 × baseline')
    expect(detail().textContent).toContain('33.3%')
    expect(detail().textContent).toContain('+50.0pp')
    expect(detail().textContent).toContain('◆ separates')
    expect(within(cv).getByRole('button', { name: /beta-2 under terse: pass@1 75.0%, n=4/ })).toBeTruthy()
    fireEvent.click(within(cv).getByRole('button', { name: 'Zoom in' }))
    expect(cv.textContent).toContain('110%')
    act(() => { fireEvent.click(within(detail()).getByRole('button', { name: 'Open 4 runs' })) })
    expect(decodeURIComponent(location.hash)).toContain('tasks:mini:beta-2:baseline')
    expect(requests).toEqual([])
  })
  it('a note node stays local to the tab', () => {
    const { container } = renderRig('#/rig/canvas', { data: { ...rigFixture(), [`/results/${MINI}/comparisons?baseline=baseline`]: CMP } })
    fireEvent.click(screen.getByRole('button', { name: '+ Note' }))
    fireEvent.change(screen.getByPlaceholderText(/short_context bites/), { target: { value: 'look at t2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }))
    expect(el(container, 'canvas').textContent).toContain('look at t2')
  })
})

describe('settings', () => {
  it('appearance drives the Rig theme, density and motion on the Rig root', () => {
    const { container } = renderRig('#/rig/settings', { data: { ...rigFixture(), ...IMPORT } })
    const panel = [...container.querySelectorAll('[data-el="appearance"]')].find((x) => x.tagName === 'SECTION')   // the titlebar button shares the id
    fireEvent.click(within(panel).getByRole('button', { name: 'paper' }))
    expect(container.querySelector('.rig').getAttribute('data-rig-theme')).toBe('light')
    fireEvent.click(within(panel).getByRole('button', { name: 'comfortable' }))
    expect(container.querySelector('.rig').getAttribute('data-rig-density')).toBe('comfortable')
    expect(localStorage.getItem('rig.density')).toBe('comfortable')
    fireEvent.click(within(panel).getByRole('button', { name: 'reduced' }))
    expect(container.querySelector('.rig').getAttribute('data-rig-motion')).toBe('off')
  })
  it('Student Lab toggle is shared with the classic app, and guidance links appear only when on', () => {
    const { container } = renderRig('#/rig/settings', { data: { ...rigFixture(), ...IMPORT } })
    const lab = [...container.querySelectorAll('[data-el="student-lab-toggle"]')].find((x) => x.tagName === 'SECTION')
    expect(lab.textContent).toContain('When on'); expect(lab.textContent).toContain('When off')
    expect(el(container, 'guidance-links')).toBeNull()
    act(() => { fireEvent.click(within(lab).getByRole('switch')) })
    expect(localStorage.getItem('hs.studentLab')).toBe('on')
    expect(el(container, 'guidance-links')).toBeTruthy()
  })
  it('says so when the Student Lab preference cannot be saved', () => {
    const { container } = renderRig('#/rig/settings', { data: { ...rigFixture(), ...IMPORT } })
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const lab = [...container.querySelectorAll('[data-el="student-lab-toggle"]')].find((x) => x.tagName === 'SECTION')
    act(() => { fireEvent.click(within(lab).getByRole('switch')) })
    expect(lab.textContent).toContain('Could not save the preference.')
    spy.mockRestore()
  })
  it('lab key: masked hint from the server, save sits behind a confirm (cancel sends nothing)', () => {
    const { container } = renderRig('#/rig/settings', { data: { ...rigFixture(), ...IMPORT } })
    const calls = stubFetch(() => undefined)
    const k = el(container, 'lab-key')
    expect(k.textContent).toContain('sk-or-v…abcd')
    fireEvent.change(within(k).getByLabelText('harnesslab key'), { target: { value: 'sk-or-new' } })
    fireEvent.click(within(k).getByRole('button', { name: 'Save server key' }))
    expect(within(k).getByRole('button', { name: 'Send to the server' })).toBeTruthy()
    fireEvent.click(within(k).getByRole('button', { name: 'Cancel' }))
    expect(calls).toEqual([])
  })
  it('disabled key actions say why: nothing to save, nothing to forget, nothing to clear', () => {
    const { container } = renderRig('#/rig/settings', { data: { ...rigFixture(), ...IMPORT, '/settings': { ...IMPORT['/settings'], key_present: false, key_hint: '' } } })
    const b = el(container, 'buddy-settings')
    expect(whyOf(within(b).getByRole('button', { name: 'Save Buddy key' }))).toBe('Paste a key first')
    expect(whyOf(within(b).getByRole('button', { name: 'Forget key' }))).toBe('Nothing to forget: no key saved')
    const n = el(container, 'narrator-key')
    expect(whyOf(within(n).getByRole('button', { name: 'Save & check' }))).toBe('Paste a key first')
    expect(whyOf(within(n).getByRole('button', { name: 'Forget' }))).toBe('Nothing to forget: no key saved')
    const k = el(container, 'lab-key')
    expect(whyOf(within(k).getByRole('button', { name: 'Save server key' }))).toBe('Paste a key first')
    expect(whyOf(within(k).getByRole('button', { name: 'Clear' }))).toBe('Nothing to clear: the server holds no key')
    expect(k.textContent).toContain('The server holds no key.')
    expect(k.textContent).not.toContain('— none')
    for (const i of container.querySelectorAll('[data-el="lab-key"] input, [data-el="buddy-settings"] input[type=password], [data-el="narrator-key"] input')) if (i.placeholder && !i.value) expect(i.placeholder).toMatch(/^(e\.g\. |Held: )/)
  })
  it('paths in use, narrator and Buddy panels, and Return to work goes back to the previous tab', () => {
    const { container } = renderRig('#/rig/home+!settings', { data: { ...rigFixture(), ...IMPORT } })
    const p = el(container, 'paths-in-use').textContent
    expect(p).toContain('/lab/data/runs'); expect(p).toContain('2 directories'); expect(p).toContain('/lab/tasks · 2')
    expect(el(container, 'narrator-key')).toBeTruthy()
    expect(el(container, 'buddy-settings').textContent).toContain('no key in this browser')
    act(() => { fireEvent.click(el(container, 'return-to-work')) })
    expect(screen.getByRole('tab', { name: /Datasets|home/i, selected: true })).toBeTruthy()
  })
})

describe('guide and package', () => {
  it('the reading guide links real specs, including a real failing run with its failure mode', () => {
    const { container } = renderRig('#/rig/guide', { data: rigFixture() })
    const g = el(container, 'start-here')
    const specs = [...g.querySelectorAll('[data-spec]')].map((b) => b.getAttribute('data-spec'))
    expect(specs[0]).toBe('home')
    expect(specs).toContain('q:mini')
    expect(specs).toContain('run:20260101-000000-a00003')      // alpha-1/baseline t2 rep 1: cut off
    expect(g.textContent).toContain('1 task of 2 has both passing and failing repeats')
    expect(g.textContent).toContain('cut off · no patch')
    expect(container.textContent).toContain('Student Lab is off')
  })
  it('reference materials list the package, which datasets are on disk, and the task issues', () => {
    const { container } = renderRig('#/rig/package', { data: rigFixture() })
    const p = el(container, 'package-contents')
    expect(p.textContent).toContain('Exercises and task probes')
    expect(p.textContent).toContain('not present on disk')
    expect(p.textContent).toContain('Captured coding sessions and capture watcher state')
    expect(within(p).getByRole('button', { name: /t2/ }).getAttribute('data-spec')).toBe('task:mini:alpha-1:baseline:t2')
  })
})
