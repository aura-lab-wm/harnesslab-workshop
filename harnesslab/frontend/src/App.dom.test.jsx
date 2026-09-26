// @vitest-environment jsdom
/* One UI: whatever address the browser arrives on, it lands in the Rig. Old console links are
   rewritten in place to the Rig document that replaced them, on load and while the Rig is open. */
import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import App, { AppBoundary, legacyToRig } from './App'
import { renderRig, resetRig } from '../test/rig-testing'

afterEach(resetRig)

describe('legacyToRig', () => {
  it.each([
    ['', '#/rig/home'],
    ['#/', '#/rig/home'],
    ['#/home', '#/rig/home'],
    ['#/studies', '#/rig/home'],
    ['#/workspace?dataset=mini&model=vendor/m1&harness=terse', '#/rig/ds:mini:vendor/m1:terse'],
    ['#/designs/hybrid?dataset=mini', '#/rig/ds:mini'],
    ['#/s/mini', '#/rig/ds:mini'],
    ['#/s/mini/overview', '#/rig/ds:mini'],
    ['#/s/mini/family?model=m1&harness=baseline', '#/rig/an:mini:family:m1:baseline'],
    ['#/s/mini/measure', '#/rig/an:mini:outcomes'],
    ['#/s/mini/attribute', '#/rig/an:mini:delta'],
    ['#/s/mini/judge', '#/rig/an:mini:judge'],
    ['#/s/mini/experiment', '#/rig/an:mini:fit'],
    ['#/s/mini/close', '#/rig/an:mini:report'],
    ['#/s/mini/run', '#/rig/an:mini:setup'],
    ['#/field/mini', '#/rig/field:mini'],
    ['#/field/mini?run=20260101-000000-aaaaaa', '#/rig/run:20260101-000000-aaaaaa'],
    ['#/run/mini/20260101-000000-aaaaaa', '#/rig/run:20260101-000000-aaaaaa'],
    ['#/span/mini/20260101-000000-aaaaaa/7', '#/rig/span:20260101-000000-aaaaaa:7'],
    ['#/compare/mini/r1/r2', '#/rig/cmp:r1:r2'],
    ['#/fork/mini/r1', '#/rig/fork:r1'],
    ['#/load', '#/rig/sources'],
    ['#/start', '#/rig/guide'],
    ['#/sources', '#/rig/sources'],
    ['#/capture', '#/rig/capture'],
    ['#/sentinel', '#/rig/sentinel'],
    ['#/canvas', '#/rig/canvas'],
    ['#/settings', '#/rig/settings'],
    ['#/package', '#/rig/package'],
    ['#/no-such-page/at/all', '#/rig/home'],
    ['#/s/my%20set/judge', '#/rig/an:my%20set:judge'],
  ])('%s -> %s', (from, to) => { expect(legacyToRig(from)).toBe(to) })

  it('leaves a Rig hash untouched', () => {
    expect(legacyToRig('#/rig/ds:mini|run:abc?dock=log')).toBe('#/rig/ds:mini|run:abc?dock=log')
  })
})

describe('App', () => {
  it('opens the Rig at the root address, rewriting the hash in place', async () => {
    const { container } = renderRig('#/', { Root: App })
    expect(location.hash).toBe('#/rig/home')
    expect(container.querySelector('.rig')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'Datasets', level: 1 })).toBeTruthy()
    // the retired console never mounts
    expect(container.querySelector('.product-workspace, .matrix-shell, .app-layout')).toBeNull()
  })

  it('an old study link lands on the analysis view that replaced it', () => {
    const { container } = renderRig('#/s/mini/judge', { Root: App })
    expect(location.hash).toBe('#/rig/an:mini:judge')
    expect(container.querySelector('.rg-doc').dataset.spec).toBe('an:mini:judge')
  })

  it('a legacy link followed while the Rig is open is rewritten and opened', () => {
    const { container } = renderRig('#/rig/home', { Root: App })
    act(() => {
      history.replaceState(null, '', '#/field/mini')
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(location.hash).toBe('#/rig/field:mini')
    expect(container.querySelector('.rg-doc').dataset.spec).toBe('field:mini')
  })

  it('offers no way back to a second interface', () => {
    renderRig('#/rig/settings', { Root: App })
    expect(document.body.textContent).not.toMatch(/classic/i)
  })
})

describe('AppBoundary', () => {
  it('a crash in the shell is a readable page with a way back, never a blank one', () => {
    let explode = true
    const Shell = () => { if (explode) throw new Error('shell went wrong'); return <p>workbench</p> }
    const warn = console.error; console.error = () => {}
    try {
      localStorage.setItem('rig.theme', 'light')
      history.replaceState(null, '', '#/rig/ds:x?max=1')
      render(<AppBoundary><Shell /></AppBoundary>)
      const crash = screen.getByRole('alert')
      expect(crash.getAttribute('data-el')).toBe('app-crash')
      expect(crash.textContent).toContain('shell went wrong')
      expect(crash.textContent).toContain('recorded runs are untouched')
      explode = false
      fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))
      expect(location.hash).toBe('#/rig/home')
      expect(localStorage.getItem('rig.theme')).toBeNull()
      expect(screen.getByText('workbench')).toBeTruthy()
    } finally { console.error = warn }
  })
})
