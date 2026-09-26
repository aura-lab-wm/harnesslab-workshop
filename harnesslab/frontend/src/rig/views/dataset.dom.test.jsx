// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture } from '../../../test/rig-testing'

afterEach(resetRig)

describe('dataset tab', () => {
  it('opens with the nine questions answered and visible', () => {
    const { container } = renderRig('#/rig/ds:mini')
    const cards = container.querySelectorAll('[data-el="question-stack"] > li')
    expect(cards).toHaveLength(9)
    expect(container.querySelector('[data-el="question-outcomes"]').textContent).toContain('How do runs fail?')
    expect(container.querySelector('[data-el="question-report"]').textContent).toContain('(pooled)')
  })
  it('condition KPIs keep the known denominator and unknowns apart', () => {
    const { container } = renderRig('#/rig/ds:mini:beta-2:baseline')
    const s = container.querySelector('[data-el="success-rate"]').textContent
    expect(s).toContain('1 / 3 known')        // t1 ✓?, t2 ×× → 1 pass, 2 fail, 1 unknown
    expect(s).toContain('1 unknown grade excluded')
    expect(container.querySelector('[data-el="mean-tokens"]').textContent).toContain('usage recorded 4/4')
    expect(container.querySelector('[data-el="condition-summary"]').textContent).toContain('beta-2')
  })
  it('model table, where-to-look-next and failure modes come from the runs + outcomes', () => {
    const { container } = renderRig('#/rig/ds:mini:alpha-1:baseline')
    expect(container.querySelector('[data-el="model-performance-table"]').textContent).toContain('3/4 · 0')
    expect(container.querySelector('[data-el="where-to-look-next"] .rg-ds-next').textContent).toMatch(/^t2mixed1 pass · 1 fail · 0 unknown · 1 failed: 1 cut off/)
    const f = container.querySelector('[data-el="failure-mode-summary"]')
    expect(f.textContent).toContain('cut off · no patch')
    expect(f.querySelector('.rg-fchip').getAttribute('title')).toContain('output limit')
  })
  it('missing usage is shown as coverage, not zero', () => {
    const { container } = renderRig('#/rig/ds:mini:alpha-1:baseline')
    expect(container.querySelector('[data-el="mean-tokens"]').textContent).toContain('usage recorded 3/4')
  })
  it('a condition with no runs offers the conditions that have runs', () => {
    const data = rigFixture()
    data['/overview'] = { ...data['/overview'], results: data['/overview'].results.map((r) => (r.name === 'mini' ? { ...r, harnesses: [...r.harnesses, 'ghost'] } : r)) }
    renderRig('#/rig/ds:mini:alpha-1:ghost', { data })
    const empty = document.querySelector('[data-el~="empty-state"]')
    expect(empty.textContent).toContain('No runs in this condition.')
    act(() => { fireEvent.click(within(empty).getByRole('button', { name: 'alpha-1 under baseline' })) })
    expect(location.hash).toContain('ds:mini:alpha-1:baseline')
  })
  it('the two unrelated panels stack; only the failure-mode comparison is side by side', () => {
    const { container } = renderRig('#/rig/ds:mini:alpha-1:baseline')
    expect(container.querySelector('.rg-ds-grid')).toBeNull()
    expect(container.querySelector('[data-el="failure-mode-summary"] .rg-g2')).toBeTruthy()
  })
  it('changing the model in the Asking… sentence retargets the tab', () => {
    renderRig('#/rig/ds:mini')
    fireEvent.change(screen.getByLabelText('Harness'), { target: { value: 'terse' } })
    expect(location.hash).toBe('#/rig/ds:mini:alpha-1:terse?c=mini:acme/alpha-1:terse')
  })
})
