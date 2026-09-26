// The import screen's reasoning, without the screen. Every judgement the general import form
// makes -- is this path importable, what will the study be called, how far along is the job,
// which of the two roads does a source arrive by -- is a function here, so it can be wrong in a
// test rather than on the page.
import { describe, it, expect } from 'vitest'
import { WATCHED, arrival, importOnly, suggestResultsDir, cleanResultsDir, detectState, progressPct,
         progressKnown, progressPhrase, verdictTile } from './importing.js'

describe('the two roads a trace arrives by', () => {
  it('names the five sources the capture watcher follows on its own', () => {
    expect([...WATCHED].sort()).toEqual(['claude_code', 'codex', 'cursor', 'gemini_cli', 'qwen_code'])
  })
  it('sends everything else down the import road', () => {
    expect(arrival('claude_code')).toBe('capture')
    expect(arrival('swe_agent')).toBe('import')
    expect(arrival('inspect')).toBe('import')
    expect(arrival('trajectory')).toBe('import')
    expect(arrival('openhands')).toBe('import')
  })
  it('does not guess about a source it has never heard of', () => {
    expect(arrival('some_new_cli')).toBe('import')
  })
  it('splits an adapter list into the ones import is the only road for', () => {
    const nine = ['inspect', 'swe_agent', 'qwen_code', 'gemini_cli', 'claude_code', 'codex', 'cursor',
                  'trajectory', 'openhands']
    expect(importOnly(nine)).toEqual(['inspect', 'swe_agent', 'trajectory', 'openhands'])
  })
})

describe('the results directory a path suggests', () => {
  // the same rule as harnesslab.__main__.suggest_results_dir, so the CLI and this screen cannot
  // file one trace under two different studies
  it('is the source plus a slug of the path', () => {
    expect(suggestResultsDir('claude_code', '/home/me/.claude/projects/my-repo'))
      .toBe('imported_claude_code_my_repo')
  })
  it('ignores a trailing slash', () => {
    expect(suggestResultsDir('codex', '/a/b/sessions/')).toBe('imported_codex_sessions')
  })
  it('is always a legal results dir name', () => {
    for (const p of ['/a/b/../weird name!.jsonl', '/', 'relative/path', '', null]) {
      const name = suggestResultsDir('inspect', p)
      expect(name).toMatch(/^[A-Za-z0-9_-]+$/)
      expect(name.startsWith('.')).toBe(false)
    }
  })
  it('falls back to a name even with no source', () => {
    expect(suggestResultsDir('', '/a/b')).toBe('imported_trace_b')
  })
})

describe('what the results-dir field accepts', () => {
  it('drops everything that would make it a path', () => {
    expect(cleanResultsDir('../escape/me')).toBe('escapeme')
    expect(cleanResultsDir('my study 1')).toBe('mystudy1')
  })
  it('never leaves a leading dot, which the backend refuses', () => {
    expect(cleanResultsDir('...hidden')).toBe('hidden')
  })
})

describe('the detector line', () => {
  it('says nothing before a path is typed', () => {
    expect(detectState(null, '').tone).toBe('idle')
    expect(detectState(undefined, '').ready).toBe(false)
  })
  it('calls a missing path what it is', () => {
    const s = detectState({ path: '/nope', exists: false, source: '', candidates: [] }, '')
    expect(s.tone).toBe('bad')
    expect(s.ready).toBe(false)
    expect(s.headline).toMatch(/no such file or directory/)
  })
  it('will not import a path no adapter recognises', () => {
    const s = detectState({ path: '/x', exists: true, is_dir: true, source: '', candidates: [] }, '')
    expect(s.tone).toBe('warn')
    expect(s.ready).toBe(false)
    expect(s.headline).toMatch(/no adapter/i)
  })
  it('lets a forced adapter override a path it could not sniff', () => {
    const s = detectState({ path: '/x', exists: true, is_dir: true, source: '', candidates: [] }, 'trajectory')
    expect(s.ready).toBe(true)
    expect(s.source).toBe('trajectory')
    expect(s.headline).toMatch(/trajectory/)
  })
  it('reports the winner and how many sessions are under it', () => {
    const s = detectState({ path: '/x', exists: true, is_dir: true, source: 'codex', sessions: 3,
                            candidates: [{ source: 'codex', confidence: 0.9, files: 3 }] }, '')
    expect(s.tone).toBe('ok')
    expect(s.ready).toBe(true)
    expect(s.source).toBe('codex')
    expect(s.headline).toMatch(/codex/)
    expect(s.detail).toMatch(/3 sessions/)
  })
  it('counts one session in the singular', () => {
    const s = detectState({ path: '/x', exists: true, is_dir: false, source: 'inspect', sessions: 1,
                            candidates: [] }, '')
    expect(s.detail).toMatch(/1 session\b/)
    expect(s.detail).not.toMatch(/1 sessions/)
  })
  it('does not pretend to know a session count it was not given', () => {
    const s = detectState({ path: '/x', exists: true, source: 'inspect', sessions: null, candidates: [] }, '')
    expect(s.ready).toBe(true)
    expect(s.detail).not.toMatch(/\bnull\b|\b0 sessions\b/)
  })
  it('says out loud when the forced adapter is not the one it sniffed', () => {
    const s = detectState({ path: '/x', exists: true, source: 'openhands', sessions: 2, candidates: [] },
                          'trajectory')
    expect(s.source).toBe('trajectory')
    expect(s.tone).toBe('warn')
    expect(s.headline).toMatch(/trajectory/)
    expect(s.headline).toMatch(/openhands/)
  })
  it('carries the per-adapter scores through for the runner-up line', () => {
    const s = detectState({ path: '/x', exists: true, source: 'claude_code', sessions: 1,
                            candidates: [{ source: 'claude_code', confidence: 1, files: 1 },
                                         { source: 'qwen_code', confidence: 0.6, files: 1 }] }, '')
    expect(s.others).toEqual([{ source: 'qwen_code', confidence: 0.6, files: 1 }])
  })
  it('surfaces an adapter that threw while sniffing', () => {
    const s = detectState({ path: '/x', exists: true, source: '', candidates: [], error: 'ValueError: bad zip' }, '')
    expect(s.detail).toMatch(/bad zip/)
  })
})

describe('the progress bar', () => {
  it('is zero before the job has counted anything, not NaN', () => {
    // /api/import/status starts at [0, 0]; dividing by that width paints an invalid style
    expect(progressPct([0, 0])).toBe(0)
    expect(progressPct(null)).toBe(0)
    expect(progressPct([3])).toBe(0)
  })
  it('is the share done, clamped to the bar', () => {
    expect(progressPct([1, 4])).toBe(25)
    expect(progressPct([4, 4])).toBe(100)
    expect(progressPct([9, 4])).toBe(100)
  })
})

describe('review: the reading line reports what was counted, and nothing else', () => {
  it('names a total only when the server actually counted one', () => {
    /* The importer hands back a generator, so how many sessions are under a path is not known
       until the last one is read. It used to report the running count as the total, which made
       this bar read 100% from the first session -- a measured-looking number for a quantity
       nobody had measured. */
    expect(progressPhrase([3, null])).toBe('3 sessions read')
    expect(progressPhrase([1, null])).toBe('1 session read')
    expect(progressPhrase([0, null])).toBe('0 sessions read')
    expect(progressPhrase([3, 10])).toBe('3 of 10 sessions read')
    expect(progressPhrase(null)).toBe('reading')
    expect(progressPhrase([])).toBe('reading')
  })

  it('draws no bar for a fraction it cannot compute', () => {
    expect(progressPct([3, null])).toBe(0)
    expect(progressKnown([3, null])).toBe(false)
    expect(progressKnown([3, 10])).toBe(true)
    expect(progressKnown(null)).toBe(false)
    expect(progressKnown([3, 0])).toBe(false)
  })
})

describe('review: the verdict tile counts runs, not nothing', () => {
  it('says nothing was imported rather than "every run carries one"', () => {
    /* Re-importing a path that is already in the study imports zero runs, so known and unknown
       are both zero and the tile read "0 / 0" under the note "every run carries one" -- a claim
       about a population of none. */
    expect(verdictTile({ outcomes_known: 0, outcomes_unknown: 0, imported: 0, skipped: 4 }))
      .toMatchObject({ value: '\u2014', note: 'nothing was imported this time' })
    expect(verdictTile({ outcomes_known: 3, outcomes_unknown: 0, imported: 3, skipped: 0 }))
      .toMatchObject({ value: '3 / 3', note: 'every run carries one', flagged: false })
    expect(verdictTile({ outcomes_known: 1, outcomes_unknown: 2, imported: 3, skipped: 0 }))
      .toMatchObject({ value: '1 / 3', note: 'the rest read as not passed', flagged: true })
  })

  it('does not invent a count the server never reported', () => {
    expect(verdictTile({ imported: 3, skipped: 0 }))
      .toMatchObject({ value: '\u2014', note: 'this import reported no verdict counts' })
  })
})
