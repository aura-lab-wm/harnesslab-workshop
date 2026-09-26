// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { pin, unpin, togglePin, isPinned, getItems, clearCase, setNote, caseMarkdown, reloadCase, pinId, storageOk } from './caseFile'

beforeEach(() => { localStorage.clear(); reloadCase() })

describe('case file', () => {
  it('pins once per id and persists to rig.case', () => {
    const it1 = { id: pinId('run', 'r1'), kind: 'run', label: 'run r1', spec: 'run:r1', run: 'r1', dir: 'd' }
    pin(it1); pin(it1)
    expect(getItems()).toHaveLength(1)
    expect(JSON.parse(localStorage.getItem('rig.case'))[0].id).toBe('run:r1')
    togglePin(it1); expect(isPinned('run:r1')).toBe(false)
  })
  it('notes, unpin, clear', () => {
    pin({ id: 'a', kind: 'answer', label: 'A' }); pin({ id: 'b', kind: 'run', label: 'B' })
    setNote('a', 'check this'); expect(getItems()[0].note).toBe('check this')
    unpin('b'); expect(getItems().map((x) => x.id)).toEqual(['a'])
    clearCase(); expect(getItems()).toEqual([])
  })
  it('exports only what was pinned, grouped, as Markdown', () => {
    const md = caseMarkdown([
      { id: 'answer:d:grader', kind: 'answer', label: 'Can I trust the grader?', value: '−14.2pp', dir: 'd', spec: 'q:d:grader' },
      { id: 'run:x', kind: 'run', label: 'run c95aff', value: 'fail', run: 'x', dir: 'd', note: 'cut off' },
    ], { oracle: 'hidden', at: 'T' })
    expect(md).toContain('## Answers (1)'); expect(md).toContain('**Can I trust the grader?** — −14.2pp')
    expect(md).toContain('## Runs (1)'); expect(md).toContain('note: cut off'); expect(md).toContain('`#/rig/q:d:grader`')
    expect(caseMarkdown([], {})).toContain('_Nothing pinned._')
  })
  it('survives blocked storage: keeps pins for the visit and says so', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    pin({ id: 'z', kind: 'run', label: 'Z' })
    expect(isPinned('z')).toBe(true); expect(storageOk()).toBe(false)
    spy.mockRestore()
  })
})
