import { describe, it, expect } from 'vitest'
import { registry, buildRegistry, getView, listDocks } from './registry'

// Every tab kind SPEC.md names must resolve to a view (real or pending) so the shell is navigable.
const KINDS = ['home', 'ds', 'an', 'task', 'run', 'span', 'cmp', 'fork', 'q', 'field', 'sources', 'capture', 'sentinel', 'canvas', 'settings', 'guide', 'package']

describe('view registry', () => {
  it('has no conflicts: one non-underscore owner per kind and dock', () => { expect(registry.conflicts).toEqual([]) })
  it('covers every kind in the spec', () => {
    for (const k of KINDS) { const v = getView(k); expect(v, k).toBeTruthy(); expect(typeof v.render, k).toBe('function') }
  })
  it('Phase 1 kinds are real views, the rest may be pending', () => {
    for (const k of ['home', 'ds', 'q']) expect(getView(k).pending, k).toBe(false)
  })
  it('has the four dock tabs in order', () => {
    expect(listDocks().map((d) => d.id)).toEqual(['buddy', 'log', 'capture', 'case'])
  })
  it('a real module overrides a pending one without touching it; two real owners conflict', () => {
    const R = () => null
    const r = buildRegistry({
      './views/_pending.jsx': { views: { run: { render: R, tag: 'old' } }, docks: { log: { render: R } } },
      './views/run.jsx': { views: { run: { render: R, tag: 'new' } }, docks: { log: { render: R, label: 'Event log' } } },
    })
    expect(r.views.run.tag).toBe('new'); expect(r.views.run.pending).toBe(false); expect(r.docks.log.label).toBe('Event log')
    expect(r.conflicts).toEqual([])
    const c = buildRegistry({ './views/a.jsx': { views: { run: { render: R } } }, './views/b.jsx': { views: { run: { render: R } } } })
    expect(c.conflicts).toHaveLength(1)
  })
  it('rejects a view without a render component', () => {
    expect(buildRegistry({ './views/x.jsx': { views: { bad: { title: () => 'x' } } } }).conflicts[0]).toMatch(/no render/)
  })
})

describe('the explicit module list stays complete', () => {
  it('registers every view file on disk (a new views/*.jsx must be added to registry.js)', async () => {
    const { readdirSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { modules } = await import('./registry.js')
    const dir = fileURLToPath(new URL('./views/', import.meta.url))
    const onDisk = readdirSync(dir).filter(f => f.endsWith('.jsx') && !f.endsWith('.test.jsx')).map(f => './views/' + f).sort()
    expect(Object.keys(modules).sort()).toEqual(onDisk)
  })
})
