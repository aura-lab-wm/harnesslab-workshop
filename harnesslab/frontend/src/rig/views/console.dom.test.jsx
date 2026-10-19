// @vitest-environment jsdom
/* Run console: rows from tool calls, risk from the replay, interventions recorded vs replayed. */
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture, miniRuns, MINI } from '../../../test/rig-testing'
import { consoleModel, interventions, testResult, RunConsole } from './console'

afterEach(resetRig)
const ID = '20260101-000000-a00002'
const base = { run_id: ID, task_id: 't2', harness_id: 'baseline', 'gen_ai.request.model': 'acme/alpha-1' }
const chat = (seq, step, calls) => ({ ...base, seq, span: 'chat', step, text: '', 'gen_ai.response.finish_reasons': ['tool_calls'], tool_calls: calls.map((name) => ({ name, arguments: {} })) })
const tool = (seq, name, args, extra = {}) => ({ ...base, seq, span: 'execute_tool', 'gen_ai.tool.name': name, args, status: 'ok', ...extra })
const spans = [
  { ...base, seq: 0, span: 'invoke_agent', status: 'start', harness: { sentinel: { enabled: true, threshold: 0.5 } } },
  chat(1, 0, ['read_file']), tool(2, 'read_file', { path: 'src/a.py' }, { result_preview: 'x' }),
  chat(3, 1, ['edit_file']), { ...base, seq: 4, span: 'edit', path: 'src/a.py', lines_added: 3, lines_removed: 1, tool: 'edit_file' }, tool(5, 'edit_file', { path: 'src/a.py' }),
  chat(6, 2, ['run_tests']), tool(7, 'run_tests', {}, { result_preview: 'exit=1\nFAILED (failures=2)', tests_passed: false }),
  chat(8, 3, ['submit']), tool(9, 'submit', {}, { result_preview: 'submitted' }),
]
const replay = [0.4, 0.3, 0.7, 0.55].map((risk, i) => ({ step: i, risk, patterns: i >= 2 ? ['submit_without_verify'] : [], explain: [], pending: [], recorded: null }))
const data = (r = replay) => ({
  ...rigFixture(),
  '/sentinel': { active: 'm', default_config: { threshold: 0.6 }, patterns: { submit_without_verify: { severity: 'high', nudge: 'Run the tests before you submit.' } }, models: [] },
  [`/results/${MINI}/runs/${ID}`]: { summary: miniRuns().find((x) => x.run_id === ID), spans, replay: r, patch: '', messages: [], issue: '' },
})

describe('console model (pure)', () => {
  it('one row per tool call, with arg, edit delta, test chip and the risk of its reply', () => {
    const m = consoleModel(spans, replay, false)
    expect(m.rows.map((r) => [r.n, r.letter, r.name, r.arg])).toEqual([[1, 'R', 'read_file', 'src/a.py'], [2, 'E', 'edit_file', 'src/a.py +3 −1'], [3, 'T', 'run_tests', ''], [4, 'S', 'submit', '']])
    expect(m.rows[2].chip).toEqual({ tone: 'bad', text: '2 failed' })
    expect(m.rows[3].chip).toEqual({ tone: 'bad', text: 'failed' })
    expect(m.rows.map((r) => r.risk)).toEqual([0.4, 0.3, 0.7, 0.55])
  })
  it('parses test output, never guesses', () => {
    expect(testResult({ result_preview: 'Ran 5 tests in 0.1s\n\nOK' })).toEqual({ tone: 'ok', text: '5 passed' })
    expect(testResult({ result_preview: '3 failed, 2 passed' })).toEqual({ tone: 'bad', text: '3 failed' })
    expect(testResult({ result_preview: 'hm' })).toBeNull()
  })
  it('replayed interventions fire when a detector first appears; a recorded verdict wins', () => {
    const m = consoleModel(spans, replay, null)
    const iv = interventions(m, { submit_without_verify: { severity: 'high', nudge: 'N' } })
    expect(iv).toHaveLength(1)
    expect(iv[0]).toMatchObject({ verdict: 'flag', id: 'submit_without_verify', source: 'replayed', text: 'N', sev: 'high', after: 7 })
    const rec = replay.map((r, i) => (i === 1 ? { ...r, recorded: { risk: 0.9, patterns: ['repeat_loop'], action: 'nudge', text: 'stop' } } : r))
    const m2 = consoleModel(spans, rec, null)
    expect(m2.rows[1].risk).toBe(0.9)
    expect(interventions(m2, {}).find((v) => v.source === 'recorded')).toMatchObject({ verdict: 'nudge', id: 'repeat_loop', text: 'stop' })
  })
  it('a replayed detector that drops out and fires again is flagged once', () => {
    const flicker = [['repeat_loop'], [], ['repeat_loop'], ['repeat_loop']].map((patterns, i) => ({ step: i, risk: 0.5, patterns, explain: [], pending: [], recorded: null }))
    const iv = interventions(consoleModel(spans, flicker, null), {})
    expect(iv.map((v) => [v.id, v.k])).toEqual([['repeat_loop', 0]])
  })
})

describe('run console catalogue', () => {
  it('names, severity and nudge of a plugin detector come from /sentinel/plugins', () => {
    const plug = replay.map((r, i) => ({ ...r, patterns: i >= 1 ? ['unverified_edit_streak'] : [] }))
    const { container } = renderRig('#/rig/run:a00002', { data: data(plug) })
    const card = container.querySelector('[data-el="intervention"]')
    expect(card.textContent).toContain('unverified_edit_streak')
    expect(card.textContent).toContain('high')
    expect(card.textContent).toContain('Run the tests now and read the failure before editing again.')
  })
})

describe('run console', () => {
  it('shows rows, risk at the playhead, the replayed intervention, detectors and counts; arrows move the playhead', () => {
    const { container } = renderRig('#/rig/run:a00002', { data: data() })
    const con = container.querySelector('[data-el="run-console"]')
    expect(con.querySelectorAll('.rg-cx-st')).toHaveLength(4)
    expect(con.textContent).toContain('recorded')
    expect(con.textContent).toContain('sentinel on')
    expect(con.textContent).toContain('threshold 0.50')            // the run's own harness config wins
    expect(con.querySelector('[data-el="console-risk"] b').textContent).toBe('0.55')
    const iv = con.querySelector('[data-el="intervention"]')
    expect(iv.textContent).toContain('flag · submit_without_verify')
    expect(iv.textContent).toContain('Run the tests before you submit.')
    expect(iv.textContent.toLowerCase()).toContain('replayed')
    expect(con.querySelector('.rg-cx-dets li.fired').textContent).toContain('step 3')
    const first = con.querySelector('.rg-cx-st[data-seq="2"] .rg-cx-rowb')
    act(() => { fireEvent.click(first) })
    expect(con.querySelector('[data-el="console-step"]').textContent).toBe('step 1 / 4')
    expect(con.querySelector('[data-el="console-risk"] b').textContent).toBe('0.40')
    act(() => { fireEvent.keyDown(con.querySelector('.rg-cx-st.cur .rg-cx-rowb'), { key: 'ArrowDown' }) })
    expect(con.querySelector('[data-el="console-step"]').textContent).toBe('step 2 / 4')
    expect(within(con).getAllByRole('listitem').length).toBeGreaterThan(4)
  })
  it('without a replay there is no invented curve', () => {
    const { container } = renderRig('#/rig/run:a00002', { data: data([]) })
    const con = container.querySelector('[data-el="run-console"]')
    expect(con.querySelector('.rg-cx-spark')).toBeNull()
    expect(con.textContent).toContain('no risk curve to draw')
  })
  it('Replay run starts the playhead at step 1 and announces risk politely', () => {
    const { container } = renderRig('#/rig/run:a00002', { data: data() })
    const con = container.querySelector('[data-el="run-console"]')
    act(() => { fireEvent.click(con.querySelector('[data-el="console-replay"]')) })
    expect(con.querySelector('[data-el="console-step"]').textContent).toBe('step 1 / 4')
    expect(con.querySelector('[data-el="console-risk"]').getAttribute('aria-live')).toBe('polite')
    expect(con.querySelectorAll('.rg-cx-st.future')).toHaveLength(3)
  })
})

describe('console model: edit pairing, placeholders, test chips', () => {
  const edit = (seq, extra = {}) => ({ ...base, seq, span: 'edit', path: 'src/a.py', lines_added: 3, lines_removed: 1, tool: 'edit_file', ...extra })
  it('pairs an edit that comes AFTER its tool row (captured ledgers) and never carries one onto a later row', () => {
    const sp = [chat(1, 0, ['edit_file']), tool(2, 'edit_file', { path: 'src/a.py' }), edit(3),
      chat(4, 1, ['edit_file']), tool(5, 'edit_file', { path: 'src/b.py' }),
      chat(6, 2, ['edit_file']), edit(7, { path: 'src/c.py' }), chat(8, 3, ['edit_file']), tool(9, 'edit_file', { path: 'src/c.py' })]
    const m = consoleModel(sp, [], null)
    expect(m.rows.map((r) => r.arg)).toEqual(['src/a.py +3 −1', 'src/b.py', 'edit_file', 'src/c.py'])
    expect(m.rows[1].edit).toBeNull()
    expect(m.rows[3].edit).toBeNull()                     // the stale edit from another reply is not attached
  })
  it('omits the delta when the edit span has no counts', () => {
    const sp = [chat(1, 0, ['edit_file']), edit(2, { line_counts_known: false, lines_added: 0, lines_removed: 0 }), tool(3, 'edit_file', { path: 'src/a.py' }),
      chat(4, 1, ['edit_file']), edit(5, { lines_added: undefined, lines_removed: undefined }), tool(6, 'edit_file', { path: 'src/a.py' })]
    expect(consoleModel(sp, [], null).rows.map((r) => r.arg)).toEqual(['src/a.py', 'src/a.py'])
  })
  it('omits the 0/0 placeholder the nebius importer writes for every SWE-agent edit', () => {
    const sp = [chat(1, 0, ['edit_file']), tool(2, 'edit_file', { path: 'memset.py' }), edit(3, { path: 'memset.py', lines_added: 0, lines_removed: 0 })]
    expect(consoleModel(sp, [], null).rows.map((r) => r.arg)).toEqual(['memset.py'])
  })
  it('"0 failed, 5 passed" is a pass', () => {
    expect(testResult({ result_preview: '0 failed, 5 passed' })).toEqual({ tone: 'ok', text: '5 passed' })
  })
  it('a reply whose tool call produced no execute_tool span keeps a placeholder row and its risk', () => {
    const sp = [chat(1, 0, ['read_file']), tool(2, 'read_file', { path: 'a' }), chat(3, 1, ['run_tests'])]
    const rp = [0.2, 0.8].map((risk, i) => ({ step: i, risk, patterns: i ? ['repeat_loop'] : [], recorded: null }))
    const m = consoleModel(sp, rp, null)
    expect(m.rows.map((r) => [r.name, r.rIdx, r.risk])).toEqual([['read_file', 0, 0.2], ['no result recorded', 1, 0.8]])
    expect(interventions(m, {})).toHaveLength(1)
  })
})

describe('run console: playhead and keys', () => {
  const run = { id: ID, task: 't2', model: 'acme/alpha-1', exit: 'done' }
  let setExt
  const Harness = () => {
    const [sel, setSel] = useState(null)
    setExt = setSel
    return <RunConsole run={run} spans={spans} replay={replay} sel={sel} onSel={setSel} verdictOf={false} />
  }
  const mount = () => renderRig('#/rig/', { data: data(), Root: Harness }).container.querySelector('[data-el="run-console"]')
  const stepText = (con) => con.querySelector('[data-el="console-step"]').textContent

  it('a finished replay hands the playhead back, and an outside selection still moves it', () => {
    vi.useFakeTimers()
    try {
      const con = mount()
      act(() => { fireEvent.click(con.querySelector('[data-el="console-replay"]')) })
      for (let i = 0; i < 6; i++) act(() => { vi.advanceTimersByTime(750) })
      expect(stepText(con)).toBe('step 4 / 4')
      expect(con.querySelector('[data-el="console-replay"]').textContent).toBe('Replay run')   // not replaying, not locked
      act(() => { setExt(2) })
      expect(stepText(con)).toBe('step 1 / 4')
    } finally { vi.useRealTimers() }
  })
  it('in reduced motion a Step playhead press does not lock the playhead', () => {
    vi.stubGlobal('matchMedia', (q) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }))
    const con = mount()
    act(() => { fireEvent.click(con.querySelector('[data-el="console-replay"]')) })
    expect(stepText(con)).toBe('step 1 / 4')
    act(() => { setExt(5) })
    expect(stepText(con)).toBe('step 2 / 4')
    expect(con.querySelectorAll('.rg-cx-st .rg-cx-rowb[aria-expanded="true"]')).toHaveLength(1)
  })
  it('arrow keys typed inside an expanded detail are not intercepted', () => {
    const con = mount()
    act(() => { fireEvent.click(con.querySelector('.rg-cx-st[data-seq="2"] .rg-cx-rowb')) })
    const xp = con.querySelector('.rg-cx-xp')
    expect(xp).not.toBeNull()
    expect(fireEvent.keyDown(xp, { key: 'ArrowDown' })).toBe(true)   // not default-prevented
    expect(stepText(con)).toBe('step 1 / 4')
  })
  it('files edited counts distinct paths of recorded edits only, not blocked or unpaired edit calls', () => {
    const sp = [chat(1, 0, ['edit_file']), { ...base, seq: 2, span: 'edit', path: 'src/a.py', lines_added: 1, lines_removed: 0 }, tool(3, 'edit_file', { path: 'src/a.py' }),
      chat(4, 1, ['edit_file']), tool(5, 'edit_file', { path: 'src/blocked.py' }, { status: 'blocked' })]
    const { container } = renderRig('#/rig/', { data: data(), Root: () => <RunConsole run={run} spans={sp} replay={[]} sel={null} onSel={() => {}} verdictOf={false} /> })
    const kv = [...container.querySelectorAll('.rg-cx-kv div')].find((d) => d.textContent.startsWith('files edited'))
    expect(kv.querySelector('b').textContent).toBe('1')
  })
  it('follows prefers-reduced-motion changes while mounted', () => {
    let cb, matches = false
    vi.stubGlobal('matchMedia', (q) => ({ get matches() { return matches }, media: q, addEventListener: (_, f) => { cb = f }, removeEventListener() {} }))
    const con = mount()
    expect(con.querySelector('[data-el="console-replay"]').textContent).toBe('Replay run')
    matches = true
    act(() => { cb() })
    expect(con.querySelector('[data-el="console-replay"]').textContent).toBe('Step playhead')
  })
  it('a selection that cites an edit span (evidence "first edit") lands on that edit row, not the row before it', () => {
    // fixture order is native: edit span seq 4, then its edit_file tool row seq 5 (row 2)
    const { container } = renderRig('#/rig/', { data: data(), Root: () => <RunConsole run={run} spans={spans} replay={replay} sel={4} onSel={() => {}} verdictOf={false} /> })
    expect(stepText(container.querySelector('[data-el="run-console"]'))).toBe('step 2 / 4')
  })
  it('draws no meter fill when the row has no risk', () => {
    const { container } = renderRig('#/rig/', { data: data(), Root: () => <RunConsole run={run} spans={spans} replay={replay.map((r, i) => (i === 3 ? { ...r, risk: null } : r))} sel={null} onSel={() => {}} verdictOf={false} /> })
    expect(container.querySelector('.rg-cx-meter i')).toBeNull()
  })
})
