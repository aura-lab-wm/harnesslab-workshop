import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { verdict, tally, tokStats, normRun, normRuns, joinOutcomes, normOverview, resolveCondition, defaultCondition, sfx, short, fmt,
  isFailureMode, runHandle, prime, peek, invalidate, resetCache, primeError, load, isOnline } from './data'
import { miniRuns, miniOutcomes } from '../../test/rig-testing'

beforeEach(() => resetCache())

describe('verdicts never count unknown as failure', () => {
  const runs = normRuns(miniRuns(), 'mini')
  it('tallies hidden: 11 pass, 4 fail, 1 unknown, rate over the 15 known', () => {
    const t = tally(runs, 'hidden')
    expect(t).toMatchObject({ p: 11, f: 4, u: 1, n: 16, known: 15 })
    expect(t.rate).toBeCloseTo(11 / 15)
  })
  it('switches suite', () => {
    expect(tally(runs, 'strengthened')).toMatchObject({ p: 10, f: 5, u: 1 })
    expect(tally(runs, 'visible')).toMatchObject({ p: 13, f: 3, u: 0 })
    expect(verdict(runs[9], 'hidden')).toBeNull()
  })
  it('rate is null when nothing is known', () => { expect(tally([{ hid: null }]).rate).toBeNull() })
})

describe('normalisation', () => {
  it('keeps missing usage as null and averages only recorded runs', () => {
    const runs = normRuns(miniRuns(), 'mini')
    expect(runs[3].in).toBeNull()
    const t = tokStats(runs)
    expect(t).toMatchObject({ n: 16, rec: 15 })
    expect(tokStats([]).mean).toBeNull()
  })
  it('an imported run (no started_at, wall 0) has cost and wall ABSENT, not $0.00', () => {
    const r = normRun({ run_id: 'real-1', task_id: 't', harness_id: 'h', model: 'm', started_at: '', wall_ms: 0, cost_usd: 0, hidden_pass: false }, 'real')
    expect(r.cost).toBeNull(); expect(r.wall).toBeNull()
    const m = normRun({ run_id: 'x', started_at: '2026', wall_ms: 10, cost_usd: 0 }, 'd')
    expect(m.cost).toBe(0)   // a measured zero stays zero
  })
  it('joins outcomes modes by run id', () => {
    const raw = miniRuns()
    const runs = joinOutcomes(normRuns(raw, 'mini'), miniOutcomes(raw))
    expect(runs[3].mode).toBe('cutoff_no_patch'); expect(runs[3].lastFinish).toBe('length')
    expect(runs[9].mode).toBe('ungraded')
    expect(isFailureMode('ungraded')).toBe(false); expect(isFailureMode('passed')).toBe(false); expect(isFailureMode('wrong_patch')).toBe(true)
  })
  it('labels mock controls and resolves conditions', () => {
    const ov = normOverview({ results: [{ name: 'a', models: ['mock', 'mock-b'], harnesses: ['x', 'baseline'] }, { name: 'b', models: ['z/m2', 'z/m1'], harnesses: ['x'] }] })
    expect(ov.results.map((r) => r.kind)).toEqual(['mock', 'recorded'])
    expect(defaultCondition(ov.results[0])).toEqual({ model: 'mock', harness: 'baseline' })
    expect(resolveCondition(ov.results[1], 'm2', 'nope')).toEqual({ dir: 'b', model: 'z/m2', harness: 'x' })
    expect(resolveCondition(ov.results[1], null, null)).toEqual({ dir: 'b', model: 'z/m1', harness: 'x' })
  })
  it('short names and run handles', () => {
    expect(short('openai/gpt-5.6-luna')).toBe('gpt-5.6-luna'); expect(short('mock')).toBe('mock')
    expect(sfx('20260909-033945-c95aff')).toBe('c95aff'); expect(sfx('real-0001-x')).toBe('real-0001-x')
    expect(runHandle({ id: '20260909-033945-c95aff' })).toBe('c95aff')
  })
  it('formats honestly', () => {
    expect(fmt.pct(null)).toBe('—'); expect(fmt.pct(0.953125)).toBe('95.3%'); expect(fmt.pp(-0.142)).toBe('−14.2pp')
    expect(fmt.usd(0)).toBe('$0.0000'); expect(fmt.usd(null)).toBe('—'); expect(fmt.usd(32722.514, 2)).toBe('$32,722.51'); expect(fmt.int(1632)).toBe('1,632')
  })
})

describe('cache', () => {
  it('prime / peek / invalidate', () => {
    prime({ '/overview': { results: [] } })
    expect(peek('/overview')).toEqual({ results: [] })
    invalidate('/over')
    expect(peek('/overview')).toBeUndefined()
  })
  it('load() always resolves, even for a failed path', async () => {
    primeError('/bad', 'boom')
    await expect(load('/bad')).resolves.toBeUndefined()
  })
})

describe('connection: a stopped server is noticed, and a restarted one heals the workbench', () => {
  afterEach(() => { vi.unstubAllGlobals(); resetCache() })
  const answer = (body, status = 200) => ({ ok: status < 400, status, statusText: 'x', headers: { get: () => 'application/json' }, json: async () => body })
  it('nobody listening marks it down; any HTTP answer, even a 404, marks it up and drops what failed', async () => {
    resetCache()
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    expect(await load('/results/a/runs')).toBeUndefined()
    expect(isOnline()).toBe(false)
    expect(peek('/results/a/runs')).toBeUndefined()
    vi.stubGlobal('fetch', vi.fn(async (url) => (String(url).includes('/nope') ? answer({ detail: 'no' }, 404) : answer([1, 2]))))
    expect(await load('/nope')).toBeUndefined()        // a 404 is the server answering
    expect(isOnline()).toBe(true)
    // the path that failed while the server was down was dropped, so it is fetched afresh
    expect(await load('/results/a/runs')).toEqual([1, 2])
  })
  it('an HTTP error alone never reports the server as down', async () => {
    resetCache()
    vi.stubGlobal('fetch', vi.fn(async () => answer({ detail: 'boom' }, 500)))
    await load('/overview')
    expect(isOnline()).toBe(true)
  })
})
