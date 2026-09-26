import { describe, it, expect } from 'vitest'
import { parseHash, toHash, parseSpec, makeSpec, withChain, specKind, encArg, decArg, isRigHash, DEFAULT_STATE } from './route'

const base = (o) => ({ ...DEFAULT_STATE, panes: [{ tabs: ['home'], active: 0 }], ...o })

describe('rig hash grammar', () => {
  it('parses the bare route to the default state', () => {
    for (const h of ['#/rig', '#/rig/', '#rig', '']) expect(parseHash(h)).toEqual(base())
  })
  it('round-trips every field', () => {
    const states = [
      base(),
      base({ panes: [{ tabs: ['home', 'ds:llma4se_live'], active: 1 }] }),
      base({ panes: [{ tabs: ['home', 'ds:x:m:h', 'q:x:grader'], active: 2 }, { tabs: ['run:c95aff~q:x:grader'], active: 0 }], focus: 1 }),
      base({ dock: 'case', pal: true, oracle: 'strengthened', theme: 'light' }),
      base({ sheet: 'tabs', dock: 'buddy', oracle: 'visible', theme: 'system' }),
      base({ cond: { dir: 'llma4se_live', model: 'deepseek/deepseek-v4-flash', harness: 'baseline' } }),
      base({ cond: { dir: 'x', model: 'glm-5.2:cloud', harness: 'a+b' } }),
      base({ lab: true }), base({ lab: false }),
      base({ max: true }), base({ max: true, panes: [{ tabs: ['home'], active: 0 }, { tabs: ['run:abc'], active: 0 }], focus: 1, dock: 'log' }),
    ]
    for (const s of states) expect(parseHash(toHash(s))).toEqual(s)
  })
  it('writes the prototype grammar: ! marks a non-first active tab, | splits panes', () => {
    const s = base({ panes: [{ tabs: ['home', 'ds:a'], active: 1 }, { tabs: ['run:abc'], active: 0 }], focus: 1, oracle: 'visible' })
    expect(toHash(s)).toBe('#/rig/home+!ds:a|run:abc?f=1&o=visible')
    expect(parseHash('#/rig/!home+ds:a').panes[0].active).toBe(0)
  })
  it('never throws on junk and clamps what it cannot use', () => {
    const s = parseHash('#/rig/+++|||?dock=nope&o=weird&f=9&theme=blue&pal=0&c=')
    expect(s).toEqual(base())
    expect(parseHash('#/rig/a|b|c').panes).toHaveLength(2)
    expect(parseHash('#/rig/a+!b?f=5').focus).toBe(0)
  })
  it('keeps the hidden suite and pane 0 implicit', () => {
    expect(toHash(base({ oracle: 'hidden', focus: 0 }))).toBe('#/rig/home')
  })
})

describe('tab specs', () => {
  it('escapes only the grammar characters, keeping / readable', () => {
    expect(makeSpec('ds', 'llma4se_live', 'deepseek/deepseek-v4-flash', 'baseline')).toBe('ds:llma4se_live:deepseek/deepseek-v4-flash:baseline')
    const s = makeSpec('ds', 'x', 'glm-5.2:cloud', 'a+b|c')
    expect(s).toBe('ds:x:glm-5.2%3Acloud:a%2Bb%7Cc')
    expect(parseSpec(s)).toEqual({ kind: 'ds', args: ['x', 'glm-5.2:cloud', 'a+b|c'], chain: null })
  })
  it('drops trailing empty args but keeps inner ones', () => {
    expect(makeSpec('ds', 'x', null, undefined)).toBe('ds:x')
    expect(makeSpec('task', 'x', '', 'h', 't')).toBe('task:x::h:t')
    expect(parseSpec('task:x::h:t').args).toEqual(['x', '', 'h', 't'])
  })
  it('carries an evidence chain that views never see in args', () => {
    const s = withChain(makeSpec('span', '20260909-033945-268be1', 8), { dir: 'llma4se_live', qid: 'outcomes' })
    expect(s).toBe('span:20260909-033945-268be1:8~q:llma4se_live:outcomes')
    expect(parseSpec(s)).toEqual({ kind: 'span', args: ['20260909-033945-268be1', '8'], chain: { dir: 'llma4se_live', qid: 'outcomes' } })
    expect(specKind(s)).toBe('span')
    expect(withChain(s, null)).toBe('span:20260909-033945-268be1:8')
    expect(parseSpec('run:x~bogus').chain).toBeNull()
  })
  it('a spec survives a full hash round trip', () => {
    const spec = withChain(makeSpec('run', 'a:b'), { dir: 'd~1', qid: 'grader' })
    const st = base({ panes: [{ tabs: ['home', spec], active: 1 }] })
    const back = parseHash(toHash(st))
    expect(back.panes[0].tabs[1]).toBe(spec)
    expect(parseSpec(back.panes[0].tabs[1])).toEqual({ kind: 'run', args: ['a:b'], chain: { dir: 'd~1', qid: 'grader' } })
  })
  it('encArg/decArg are inverses and decArg never throws', () => {
    for (const x of ['a', 'a:b', '100%', 'x y', '#!~=&?']) expect(decArg(encArg(x))).toBe(x)
    expect(decArg('%E0%A4%A')).toBe('%E0%A4%A')
  })
  it('recognises rig hashes only', () => {
    expect(isRigHash('#/rig/')).toBe(true); expect(isRigHash('#/rig?pal=1')).toBe(true); expect(isRigHash('#/rig')).toBe(true)
    expect(isRigHash('#/rigs')).toBe(false); expect(isRigHash('#/')).toBe(false)
  })
})
