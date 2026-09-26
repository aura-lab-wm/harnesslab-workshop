// @vitest-environment jsdom
/* Trajectory family: task investigator, run, span, compare, fork, field, event-log dock.
   Everything renders from rigFixture() (hand-countable "mini") plus two primed ledgers:
   a00003 (alpha-1 · baseline · t2 · rep 1, hidden ×, mode cut off) modelled on the real
   llma4se_live run 20260909-034622-f74e28, and a00002 (same cell, rep 0, hidden ✓, one edit). */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture, miniRuns, MINI } from '../../../test/rig-testing'
import { failureStory, pairCounts, replyNumbers, spanDesc } from './trajectory'

afterEach(resetRig)

const ID3 = '20260101-000000-a00003', ID2 = '20260101-000000-a00002'
const base = { run_id: ID3, task_id: 't2', harness_id: 'baseline', 'gen_ai.request.model': 'acme/alpha-1' }
const HARNESS = { id: 'baseline', tools: ['read_file', 'edit_file', 'run_tests', 'submit'], max_steps: 20, max_tokens_per_call: 2048 }
const cutSpans = [
  { ...base, seq: 0, ts: '2026-01-01T00:00:00.000Z', span: 'invoke_agent', status: 'start', harness: HARNESS, harness_hash: 'e2fb4042aa89', seed: 7313803, repeat_index: 1 },
  { ...base, seq: 1, ts: '2026-01-01T00:00:02.500Z', span: 'chat', 'gen_ai.usage.input_tokens': 847, 'gen_ai.usage.output_tokens': 100, 'gen_ai.response.finish_reasons': ['tool_calls'], duration_ms: 2500, cost_usd: 0.0001, step: 0, text: '', tool_calls: [{ name: 'read_file', arguments: { path: 'src/a.py' } }, { name: 'read_file', arguments: { path: 'tests/test_a.py' } }] },
  { ...base, seq: 2, ts: '2026-01-01T00:00:02.510Z', span: 'execute_tool', 'gen_ai.tool.name': 'read_file', args: { path: 'src/a.py' }, status: 'ok', duration_ms: 1, result_preview: '1| def a(): pass' },
  { ...base, seq: 3, ts: '2026-01-01T00:00:02.520Z', span: 'execute_tool', 'gen_ai.tool.name': 'read_file', args: { path: 'tests/test_a.py' }, status: 'ok', duration_ms: 3, result_preview: '1| import unittest' },
  { ...base, seq: 4, ts: '2026-01-01T00:00:36.000Z', span: 'chat', 'gen_ai.usage.input_tokens': 1488, 'gen_ai.usage.output_tokens': 2048, 'gen_ai.response.finish_reasons': ['length'], duration_ms: 33400, cost_usd: 0.0004, step: 1, text: '', tool_calls: [] },
  { ...base, seq: 5, ts: '2026-01-01T00:00:36.100Z', span: 'grade', visible: false, hidden: false, strong: false, tests_modified: false },
  { ...base, seq: 6, ts: '2026-01-01T00:00:36.200Z', span: 'invoke_agent', status: 'end', exit_reason: 'no_action', hidden_pass: false, total_tokens: 4483 },
]
const b2 = { ...base, run_id: ID2 }
const passSpans = [
  { ...b2, seq: 0, ts: '2026-01-01T00:00:00.000Z', span: 'invoke_agent', status: 'start', harness: HARNESS, seed: 7313803, repeat_index: 0 },
  { ...b2, seq: 1, ts: '2026-01-01T00:00:02.000Z', span: 'chat', 'gen_ai.usage.input_tokens': 800, 'gen_ai.usage.output_tokens': 90, 'gen_ai.response.finish_reasons': ['tool_calls'], duration_ms: 2000, tool_calls: [{ name: 'read_file', arguments: { path: 'src/a.py' } }, { name: 'read_file', arguments: { path: 'tests/test_a.py' } }] },
  { ...b2, seq: 2, ts: '2026-01-01T00:00:02.010Z', span: 'execute_tool', 'gen_ai.tool.name': 'read_file', args: { path: 'src/a.py' }, status: 'ok' },
  { ...b2, seq: 3, ts: '2026-01-01T00:00:02.020Z', span: 'execute_tool', 'gen_ai.tool.name': 'read_file', args: { path: 'tests/test_a.py' }, status: 'ok' },
  { ...b2, seq: 4, ts: '2026-01-01T00:00:05.000Z', span: 'chat', 'gen_ai.usage.input_tokens': 900, 'gen_ai.usage.output_tokens': 120, 'gen_ai.response.finish_reasons': ['tool_calls'], duration_ms: 3000, tool_calls: [{ name: 'edit_file', arguments: { path: 'src/a.py' } }] },
  { ...b2, seq: 5, ts: '2026-01-01T00:00:05.010Z', span: 'execute_tool', 'gen_ai.tool.name': 'edit_file', args: { path: 'src/a.py' }, status: 'ok' },
  { ...b2, seq: 6, ts: '2026-01-01T00:00:05.020Z', span: 'edit', path: 'src/a.py', lines_added: 2, lines_removed: 1, tool: 'edit_file' },
  { ...b2, seq: 7, ts: '2026-01-01T00:00:06.000Z', span: 'grade', visible: true, hidden: true, strong: true, tests_modified: false },
  { ...b2, seq: 8, ts: '2026-01-01T00:00:06.100Z', span: 'invoke_agent', status: 'end', exit_reason: 'submitted', total_tokens: 1910 },
]
const summaryOf = (id) => miniRuns().find((r) => r.run_id === id)
function data(extra = {}) {
  return {
    ...rigFixture(),
    [`/results/${MINI}/runs/${ID3}`]: { summary: summaryOf(ID3), spans: cutSpans, patch: '', messages: [{ role: 'system', content: 'You are an engineer.' }, { role: 'user', content: 'Fix t2.' }], issue: '# Issue: t2 is broken\n', replay: [] },
    [`/results/${MINI}/runs/${ID2}`]: { summary: { ...summaryOf(ID2), files_touched: ['src/a.py'], lines_added: 2, lines_removed: 1, patch_bytes: 90 }, spans: passSpans, patch: '--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n-def a(): pass\n+def a():\n+    return 1\n', messages: [], issue: '# Issue: t2 is broken\n' },
    ...extra,
  }
}
const CUT = 'Run ended by the harness after reply #2 hit the 2,048-token output limit (finish_reason length) with no tool call; no edit was made, so the hidden suite ran on the original code.'
const q = (c, el) => c.querySelector(`[data-el~="${el}"]`)

describe('ledger helpers (pure)', () => {
  it('derives the cut-off finding from spans, never from a hard-coded run', () => {
    const run = { id: ID3, hid: false, vis: false, str: false, mode: 'cutoff_no_patch', exit: 'no_action', steps: 2 }
    const s = failureStory(run, { spans: cutSpans, summary: {} }, 'hidden', {})
    expect(s.text).toBe(CUT)
    expect(s.refs.map((r) => r.seq)).toEqual([4, 6, 5])
    // without the harness limit in the start span it says what it knows, not 2,048
    const noCfg = failureStory(run, { spans: cutSpans.map((x) => (x.seq === 0 ? { ...x, harness: {} } : x)) }, 'hidden', {})
    expect(noCfg.text).toContain('hit the per-call output limit at 2,048 output tokens')
    // a pass or an unknown grade has no failure story
    expect(failureStory({ ...run, hid: true, mode: 'passed' }, { spans: cutSpans }, 'hidden', {})).toBeNull()
    expect(failureStory({ ...run, hid: null, mode: 'ungraded' }, { spans: cutSpans }, 'hidden', {})).toBeNull()
  })
  it('numbers replies and describes spans', () => {
    const n = replyNumbers(cutSpans)
    expect([n.get(1), n.get(4)]).toEqual([1, 2])
    expect(spanDesc(cutSpans[4], 2)).toBe('reply #2 · no tool call')
    expect(spanDesc(cutSpans[1], 1)).toBe('reply #1 → read_file, read_file')
  })
  it('counts pair classes over a run index', () => {
    // mini: 8 cells × C(2,2) = 8 same-cell pairs; 4 (model,task) groups × 2×2 cross-harness = 16
    const R = miniRuns().map((r) => ({ model: r.model, harness: r.harness_id, task: r.task_id }))
    expect(pairCounts(R)).toEqual({ same: 8, cross: 16 })
  })
})

describe('task investigator', () => {
  it('shows the attempt strip with failure chips, the summary with the mode meaning, and the concrete finding', () => {
    const { container, requests } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1', { data: data() })
    expect(q(container, 'asking-sentence')).toBeTruthy()
    expect(q(container, 'task-heading').textContent).toContain('2 attempts')
    expect(q(container, 'task-details').textContent).toContain('2 of 2')
    const tiles = within(q(container, 'attempt-strip')).getAllByRole('button')
    expect(tiles.map((t) => t.textContent.slice(0, 6))).toEqual(['Run 01', 'Run 02'])
    expect(tiles[1].getAttribute('aria-pressed')).toBe('true')
    expect(tiles[1].querySelector('.rg-fchip').textContent).toBe('cut off · no patch')
    expect(tiles[0].querySelector('.rg-fchip')).toBeNull()                    // a pass has no chip
    expect(tiles[1].textContent).toContain('— tok')                          // missing usage is "—", not 0
    const sum = q(container, 'run-summary')
    expect(sum.textContent).toContain(ID3)
    expect(q(container, 'failure-mode').querySelector('.rg-fchip').getAttribute('title')).toContain('The last model reply hit the per-call output limit')
    expect(q(container, 'failure-finding').textContent).toContain(CUT)
    // the story sits in the selected attempt, above the tabs, once — not repeated in Findings
    expect(sum.contains(q(container, 'failure-finding'))).toBe(true)
    expect(q(container, 'findings').querySelector('[data-el="failure-finding"]')).toBeNull()
    expect(within(q(container, 'run-actions')).getByText('Compare with passing run')).toBeTruthy()
    expect(requests).toEqual([])
  })
  it('with the sidebar open, the task list lives in the sidebar under Trajectories and switches task in place', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1', { data: data(), storage: { 'rig.nav': 'open' } })
    const nav = within(container.querySelector('[data-el="nav-tasks"]'))
    const btns = nav.getAllByRole('button')
    expect(btns[0].textContent).toMatch(/^All tasks/)
    const cur = btns.find((b) => b.getAttribute('aria-current') === 'page')
    expect(cur.textContent).toMatch(/^t2/)
    const other = btns.find((b, i) => i > 0 && b !== cur)
    act(() => { fireEvent.click(other) })
    expect(location.hash).toMatch(/^#\/rig\/task:mini:alpha-1:baseline:t\d$/)
    expect(location.hash).not.toContain('+')                                    // replaced in place, not a new tab
    act(() => { fireEvent.click(within(container.querySelector('[data-el="nav-tasks"]')).getAllByRole('button')[0]) })
    expect(location.hash).toBe('#/rig/tasks:mini:alpha-1:baseline')
  })
  it('facets rewrite the hash; the timeline counts and expands recorded events', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1', { data: data() })
    const tabs = within(q(container, 'inv-tabs')).getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Findings', 'Timeline7', 'Patch', 'Task', 'Notes', 'Messages', 'Raw record', 'Compare'])
    act(() => { fireEvent.click(tabs[1]) })
    expect(location.hash).toBe('#/rig/task:mini:alpha-1:baseline:t2:1:timeline')
    const list = q(container, 'event-list')
    const row = within(list).getByRole('button', { name: /reply #2 · no tool call/ })
    act(() => { fireEvent.click(row) })
    expect(row.getAttribute('aria-expanded')).toBe('true')
    expect(list.textContent).toContain('finish length')
    expect(list.textContent).toContain('1,488 in / 2,048 out')
  })
  it('patch, task, messages, raw and compare facets render from the recording', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1:patch', { data: data() })
    expect(q(container, 'patch-view').textContent).toContain('No patch: the run was cut off before it reached an edit.')
    act(() => { fireEvent.click(screen.getByRole('tab', { name: 'Task' })) })
    expect(q(container, 'task-text').textContent).toContain('t2 is broken')
    act(() => { fireEvent.click(screen.getByRole('tab', { name: 'Messages' })) })
    expect(q(container, 'messages').textContent).toContain('You are an engineer.')
    act(() => { fireEvent.click(screen.getByRole('tab', { name: 'Raw record' })) })
    expect(q(container, 'raw-record').textContent).toContain('"run_id": "20260101-000000-a00003"')
    act(() => { fireEvent.click(screen.getByRole('tab', { name: 'Compare' })) })
    act(() => { fireEvent.click(within(q(container, 'choose-attempt')).getByRole('button', { name: 'Compare' })) })
    expect(location.hash).toContain(`cmp:${ID3}:${ID2}`)
  })
  it('switching attempt keeps the facet; the passing attempt shows its diff', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1:patch', { data: data() })
    act(() => { fireEvent.click(within(q(container, 'attempt-strip')).getAllByRole('button')[0]) })
    expect(location.hash).toBe('#/rig/task:mini:alpha-1:baseline:t2:0:patch')
    expect(q(container, 'patch-view').textContent).toContain('+    return 1')
    expect(q(container, 'failure-finding')).toBeNull()
  })
  it('notes save to the classic review key and mark reviewed; export review writes Markdown with the failure mode', async () => {
    const blobs = []
    vi.stubGlobal('URL', Object.assign(Object.create(URL), { createObjectURL: vi.fn((b) => { blobs.push(b); return 'blob:x' }), revokeObjectURL: vi.fn() }))
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1:notes', { data: data() })
    const notes = q(container, 'notes')
    expect(within(q(container, 'run-actions')).queryByRole('button', { name: 'Export review' })).toBeNull()   // one Export review on this screen
    fireEvent.change(within(notes).getByRole('textbox'), { target: { value: 'cut off at #4' } })
    act(() => { fireEvent.click(within(notes).getByRole('button', { name: 'Save note' })) })
    const key = `hs.review.${JSON.stringify([MINI, ID3])}`
    expect(JSON.parse(localStorage.getItem(key)).text).toBe('cut off at #4')
    act(() => { fireEvent.click(within(notes).getByRole('switch', { name: /Mark reviewed/ })) })
    expect(JSON.parse(localStorage.getItem(key)).status).toBe('reviewed')
    act(() => { fireEvent.click(within(notes).getByRole('button', { name: 'Export review' })) })
    expect(blobs).toHaveLength(1)
    const md = await blobs[0].text()
    expect(md).toContain('# HarnessLab investigation')
    expect(md).toContain('Failure mode (hidden suite): cut off · no patch')
    expect(md).toContain('Finding: ' + CUT)
    expect(md).toContain('Review status: reviewed')
    expect(md).toContain('cut off at #4')
  })
  it('an unknown task is a not-found document, not a crash', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t9', { data: data() })
    expect(q(container, 'not-found')).toBeTruthy()
  })
})

describe('run, span, event log', () => {
  it('run: facts from the index and ledger, scrubber, expandable events, replay clearly labelled', () => {
    const { container, requests } = renderRig(`#/rig/run:a00003`, { data: data() })
    const facts = q(container, 'run-facts')
    expect(facts.textContent).toContain('7313803')                  // seed from the invoke_agent start span
    expect(facts.textContent).toContain('2,048')                    // max tokens / call from the harness config
    expect(facts.textContent).toContain('no_action')
    expect(q(container, 'failure-finding').textContent).toContain(CUT)
    expect(q(container, 'timeline-scrubber').querySelectorAll('.rg-traj-blk')).toHaveLength(7)
    // model replies ride the top row, everything the harness did the bottom one; the key names what is drawn
    const scrub = q(container, 'timeline-scrubber')
    const chats = scrub.querySelectorAll('.rg-traj-blk.chat')
    expect(chats.length).toBeGreaterThan(0)
    expect(scrub.querySelectorAll('.rg-traj-blk.r1')).toHaveLength(chats.length)
    expect(scrub.querySelector('.rg-traj-scrubkey').textContent).toContain('model call')
    act(() => { fireEvent.change(within(q(container, 'timeline-scrubber')).getByRole('slider'), { target: { value: '4' } }) })
    expect(within(q(container, 'event-list')).getByRole('button', { name: /^#04/ }).getAttribute('aria-expanded')).toBe('true')
    act(() => { fireEvent.click(screen.getByRole('button', { name: /Replay as live/ })) })
    const live = q(container, 'live-run')
    expect(live.textContent).toContain('REPLAY')
    expect(live.textContent).toContain('Nothing is running')
    expect(live.textContent).toMatch(/step 1 \/ 7/)
    expect(requests).toEqual([])
  })
  it('span: a model call shows finish_reason prominently and flags length', () => {
    const { container } = renderRig(`#/rig/span:a00003:4`, { data: data() })
    const fr = q(container, 'finish-reason')
    expect(fr.textContent).toContain('length')
    expect(fr.textContent).toContain('cut off at output limit')
    expect(fr.textContent).toContain('2,048 output tokens against max_tokens_per_call 2,048')
    expect(q(container, 'span-detail').textContent).toContain('#2 of 2')
    act(() => { fireEvent.click(screen.getByRole('button', { name: '↓ next' })) })
    expect(location.hash).toBe('#/rig/span:a00003:5')
    expect(q(container, 'span-detail').textContent).toContain('hidden× fail')
  })
  it('span: an unknown seq is not found', () => {
    const { container } = renderRig(`#/rig/span:a00003:99`, { data: data() })
    expect(q(container, 'not-found').textContent).toContain('no event #99')
  })
  it('event log dock follows the focused run', () => {
    const { container } = renderRig(`#/rig/run:a00003?dock=log`, { data: data() })
    const log = q(container, 'event-log')
    expect(log.textContent).toContain(ID3)
    expect(log.querySelectorAll('.rg-traj-logit')).toHaveLength(7)
    act(() => { fireEvent.click(within(log).getByRole('button', { name: /reply #2/ })) })
    expect(location.hash).toContain('span:20260101-000000-a00003:4')
  })
})

describe('compare and fork', () => {
  it('compare: pair class with counts, both headers, first divergence and aligned steps', () => {
    const { container } = renderRig(`#/rig/cmp:${ID3}:${ID2}`, { data: data() })
    const pc = q(container, 'pair-class')
    expect(within(pc).getByRole('button', { name: 'repeat of same cell · 8' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(pc).getByRole('button', { name: 'cross-harness · 16' })).toBeTruthy()
    expect(within(pc).getByRole('button', { name: /seed-paired · this pair/ })).toBeTruthy()   // both ledgers carry seed 7313803
    expect(q(container, 'pair-headers').textContent).toContain('cut off · no patch')
    const dv = q(container, 'divergence')
    expect(dv.textContent).toContain('step 4')                        // reply → read,read / read / read match; then reply(no call) vs reply → edit_file
    expect(dv.querySelector('tr.sel').textContent).toContain('reply · no tool call · length')
    act(() => { fireEvent.click(within(pc).getByRole('button', { name: '⇄ Swap' })) })
    expect(location.hash).toBe(`#/rig/cmp:${ID2}:${ID3}`)
  })
  it('compare with no runs opens from two pinned case-file runs', () => {
    const pins = JSON.stringify([{ id: `run:${ID3}`, kind: 'run', label: 'a', run: ID3 }, { id: `run:${ID2}`, kind: 'run', label: 'b', run: ID2 }])
    renderRig('#/rig/cmp', { data: data(), storage: { 'rig.case': pins } })
    act(() => { fireEvent.click(screen.getByRole('button', { name: /Compare a00003 ↔ a00002/ })) })
    expect(location.hash).toBe(`#/rig/cmp:${ID3}:${ID2}`)
  })
  it('fork is labelled not implemented; running it is unavailable (not a broken button) with real alternatives; discard resets the draft', () => {
    const { container } = renderRig(`#/rig/fork:a00003:2`, { data: data() })
    const f = q(container, 'fork-step')
    expect(f.textContent).toContain('not implemented · proposed capability')
    expect(within(f).queryByRole('button', { name: /Run the fork/ })).toBeNull()
    const un = q(container, 'fork-unavailable')
    expect(un.textContent).toContain('Running this fork is not available')
    expect(un.textContent).toContain('/api/fork')
    expect(within(un).getByRole('button', { name: 'Compare with repeat a00002' }).getAttribute('data-spec')).toBe(`cmp:${ID3}:${ID2}`)
    expect(within(un).getByRole('button', { name: 'Plan new runs of this condition' }).getAttribute('data-spec')).toBe('an:mini:setup:alpha-1:baseline')
    const ta = within(f).getByRole('textbox')
    expect(ta.value).toContain('"path": "src/a.py"')
    fireEvent.change(ta, { target: { value: '{"path":"other.py"}' } })
    expect(ta.value).toBe('{"path":"other.py"}')
    act(() => { fireEvent.click(within(f).getByRole('button', { name: 'Discard draft' })) })
    expect(within(f).getByRole('textbox').value).toContain('"path": "src/a.py"')
    expect(f.textContent).toContain('dropped')                        // steps after the fork
    act(() => { fireEvent.click(within(f).getByRole('button', { name: 'drop the step' })) })
    expect(within(f).queryByRole('textbox')).toBeNull()               // nothing to edit: said, not a disabled field
    expect(f.textContent).toContain('Dropping removes step #2; there is nothing to edit.')
  })
})

describe('field run list', () => {
  it('totals, verdict + failure-mode filters with chips, and search', () => {
    const { container } = renderRig('#/rig/field:mini', { data: data() })
    expect(q(container, 'field-totals').textContent).toMatch(/16 in scope.*✓ 11 passed.*× 4 failed.*\? 1 unknown/)
    const chips = q(container, 'failure-mode-filter')
    act(() => { fireEvent.click(within(chips).getByRole('button', { name: /cut off · no patch 1/ })) })
    const rows = q(container, 'field-run-list').querySelectorAll('tbody tr')
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain('a00003')
    act(() => { fireEvent.click(within(chips).getByRole('button', { name: 'any' })) })
    act(() => { fireEvent.click(screen.getByRole('button', { name: '? unknown' })) })
    expect(q(container, 'field-run-list').querySelectorAll('tbody tr')).toHaveLength(1)
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'all' })) })
    fireEvent.change(screen.getByPlaceholderText(/Search run id/), { target: { value: 'zzz' } })
    expect(screen.getByText('No run matches.')).toBeTruthy()
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Clear search and filters' })) })   // the way out of an empty result
    expect(q(container, 'field-run-list').querySelectorAll('tbody tr')).toHaveLength(16)
  })
  it('paginates beyond 20 runs', () => {
    const big = Array.from({ length: 45 }, (_, i) => ({ ...miniRuns()[i % 16], run_id: `20260102-000000-${(0xb00000 + i).toString(16)}`, repeat_index: i }))
    const d = data()
    d['/overview'] = { ...d['/overview'], results: [...d['/overview'].results, { name: 'big', runs: 45, harnesses: ['baseline', 'terse'], models: ['acme/alpha-1', 'acme/beta-2'], tasks: ['t1', 't2'], pass_rate: 0.7, updated: '' }] }
    d['/results/big/runs'] = big
    d['/outcomes/big'] = { runs: {}, counts: {}, modes: [] }
    const { container } = renderRig('#/rig/field:big', { data: d })
    expect(container.querySelectorAll('[data-el="field-run-list"] tbody tr')).toHaveLength(20)
    expect(screen.getByText('page 1 / 3')).toBeTruthy()
    // on page 1 there is nowhere back to go: the arrows are hidden, not disabled
    expect(screen.queryByRole('button', { name: 'First page' })).toBeNull()
    expect(screen.queryByRole('button', { name: '← prev' })).toBeNull()
    expect(container.querySelectorAll('.rig button:disabled')).toHaveLength(0)
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Last page' })) })
    expect(container.querySelectorAll('[data-el="field-run-list"] tbody tr')).toHaveLength(5)
    expect(screen.getByText('41–45 of 45')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'next →' })).toBeNull()
    expect(screen.getByRole('button', { name: '← prev' })).toBeTruthy()
  })
})

describe('dead ends are closed', () => {
  it('patch: a cut-off run says why there is no diff and offers where it stopped, a passing run, and Buddy', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1:patch', { data: data() })
    const pv = q(container, 'patch-view')
    expect(pv.textContent).toContain('Reply #2 hit the 2,048-token output limit with no tool call, and the harness ended the run (no_action).')
    expect(within(pv).getByRole('button', { name: 'Open reply #2, where it stopped' }).getAttribute('data-spec')).toBe(`span:${ID3}:4`)
    expect(within(pv).getByRole('button', { name: 'Compare with passing run a00002' }).getAttribute('data-spec')).toBe(`cmp:${ID3}:${ID2}`)
    act(() => { fireEvent.click(within(pv).getByRole('button', { name: 'Ask Buddy about this run' })) })
    expect(location.hash).toContain('dock=buddy')
  })
  it('notes: the placeholder reads as an example and is not the only hint', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1:notes', { data: data() })
    const ta = within(q(container, 'notes')).getByRole('textbox')
    expect(ta.getAttribute('placeholder')).toMatch(/^e\.g\. /)
    expect(document.getElementById(ta.getAttribute('aria-describedby')).textContent).toContain('What you observed, the event that supports it')
    expect(within(q(container, 'notes')).getByRole('textbox', { name: /Your note on run a00003/ })).toBe(ta)
  })
  it('a ledger still loading or failed: Export review is disabled WITH its reason, and the story offers Retry', async () => {
    const { container } = renderRig('#/rig/task:mini:beta-2:baseline:t2:0', { data: data() })   // a0000a: detail not primed -> 404
    const why = () => { const ex = within(q(container, 'run-summary')).getByRole('button', { name: 'Export review' }); expect(ex.disabled).toBe(true); return document.getElementById(ex.getAttribute('aria-describedby')).textContent }
    expect(why()).toBe('Waiting for the ledger to load.')
    await within(q(container, 'run-summary')).findByRole('button', { name: 'Retry' })
    expect(why()).toContain('Needs the ledger, which did not load')
  })
  it('messages: no conversation captured offers the timeline instead', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:0:messages', { data: data() })
    const m = q(container, 'messages')
    expect(m.textContent).toContain('No conversation captured.')
    act(() => { fireEvent.click(within(m).getByRole('button', { name: 'Replies and tool calls (Timeline)' })) })
    expect(location.hash).toBe('#/rig/task:mini:alpha-1:baseline:t2:0:timeline')
  })
  it('task nav: a filter with no match offers to clear it', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t2:1', { data: data() })
    const nav = q(container, 'task-nav')
    fireEvent.change(within(nav).getByRole('searchbox'), { target: { value: 'zzz' } })
    act(() => { fireEvent.click(within(nav).getByRole('button', { name: 'Clear filter' })) })
    expect(nav.querySelectorAll('.rg-traj-nav-it')).toHaveLength(2)
  })
  it('an unknown task names the tasks that exist and links to them', () => {
    const { container } = renderRig('#/rig/task:mini:alpha-1:baseline:t9', { data: data() })
    const nf = q(container, 'not-found')
    expect(nf.textContent).toContain('alpha-1 has no attempts at t9 under baseline.')
    expect(within(nf).getByRole('button', { name: 'Open t1' }).getAttribute('data-spec')).toBe('task:mini:alpha-1:baseline:t1')
    expect(within(nf).getByRole('button', { name: 'All task outcomes of this condition' })).toBeTruthy()
  })
  it('a run with no ledger says what is still real and where to go', () => {
    const ID5 = '20260101-000000-a00005'
    const { container } = renderRig('#/rig/run:a00005', { data: data({ [`/results/${MINI}/runs/${ID5}`]: { summary: summaryOf(ID5), spans: [] } }) })
    const e = q(container, 'empty-state')
    expect(e.textContent).toContain('No ledger recorded for this run.')
    expect(within(e).getByRole('button', { name: 'See it beside the other attempts' }).getAttribute('data-spec')).toBe('task:mini:alpha-1:terse:t1:1')
    expect(within(e).getByRole('button', { name: 'Every run of mini' })).toBeTruthy()
    expect(q(container, 'run-facts').textContent).toContain('submitted')   // the index row is still shown
  })
  it('span: prev is hidden on the first event and next on the last; a missing event offers the last one', () => {
    renderRig('#/rig/span:a00003:0', { data: data() })
    expect(screen.queryByRole('button', { name: '↑ prev' })).toBeNull()
    expect(screen.getByRole('button', { name: '↓ next' })).toBeTruthy()
    resetRig()
    renderRig('#/rig/span:a00003:6', { data: data() })
    expect(screen.queryByRole('button', { name: '↓ next' })).toBeNull()
    expect(screen.getByRole('button', { name: '↑ prev' })).toBeTruthy()
    resetRig()
    const { container } = renderRig('#/rig/span:a00003:99', { data: data() })
    const nf = q(container, 'not-found')
    expect(nf.textContent).toContain('numbered #0 to #6')
    expect(within(nf).getByRole('button', { name: 'Open the last event (#6)' }).getAttribute('data-spec')).toBe(`span:${ID3}:6`)
  })
  it('an unknown run offers datasets and search, not just an apology', () => {
    const { container } = renderRig('#/rig/run:zzzzzz', { data: data() })
    const nf = q(container, 'not-found')
    expect(nf.textContent).toContain('No run matches “zzzzzz”.')
    expect(within(nf).getByRole('button', { name: 'Choose a dataset' })).toBeTruthy()
    expect(within(nf).getByRole('button', { name: /Search everything/ })).toBeTruthy()
  })
  it('compare with one missing run offers a real pair for the run that exists', () => {
    const { container } = renderRig(`#/rig/cmp:a00003:zzzzzz`, { data: data() })
    const nf = q(container, 'not-found')
    expect(nf.textContent).toContain('No run matches “zzzzzz”, so there is nothing to compare a00003 with.')
    expect(within(nf).getByRole('button', { name: 'Compare a00003 with a00002 instead' }).getAttribute('data-spec')).toBe(`cmp:${ID3}:${ID2}`)
    expect(within(nf).getByRole('button', { name: 'Pick a pair from the case file' }).getAttribute('data-spec')).toBe('cmp')
  })
  it('compare picker: nothing pinned offers a run list and the case file; one pin explains the disabled Compare', () => {
    const { container } = renderRig('#/rig/cmp', { data: data() })
    const e = q(container, 'empty-state')
    expect(e.textContent).toContain('No runs pinned yet.')
    expect(within(e).getByRole('button', { name: 'Browse every run of mini' }).getAttribute('data-spec')).toBe('field:mini')
    act(() => { fireEvent.click(within(e).getByRole('button', { name: 'Open the case file' })) })
    expect(location.hash).toContain('dock=case')
    resetRig()
    const pins = JSON.stringify([{ id: `run:${ID3}`, kind: 'run', label: 'a', run: ID3 }])
    renderRig('#/rig/cmp', { data: data(), storage: { 'rig.case': pins } })
    const b = screen.getByRole('button', { name: /^Compare/ })
    expect(b.disabled).toBe(true)
    expect(document.getElementById(b.getAttribute('aria-describedby')).textContent).toBe('Pin one more run first.')
    expect(screen.getByRole('button', { name: 'Open run a00003' })).toBeTruthy()
  })
  it('event log with no focused run offers to open one', () => {
    const { container } = renderRig('#/rig/home?dock=log', { data: data() })
    const log = q(container, 'event-log')
    expect(log.textContent).toContain('No run is open in the focused tab.')
    expect(within(log).getByRole('button', { name: 'Open a failed run of mini (a00003)' }).getAttribute('data-spec')).toBe(`run:${ID3}`)
    expect(within(log).getByRole('button', { name: 'Every run of mini' })).toBeTruthy()
  })
})
