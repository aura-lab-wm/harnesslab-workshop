// @vitest-environment jsdom
/* The worked example CONTRACT.md points Phase 2 at: how to dom-test a Rig view.
   renderRig(hash) renders the whole shell with primed, hand-countable data (testing.jsx), so a
   view test exercises the real registry, context, hash and data layer — no mocks of our own code. */
import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture } from '../../../test/rig-testing'

afterEach(resetRig)

describe('home · dataset library', () => {
  it('lists every dataset from /api/overview with its kind and stats', () => {
    const { container, requests } = renderRig('#/rig/home')
    const list = container.querySelector('[data-el="dataset-list"]')
    const rows = within(list).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.querySelector('b').textContent)).toEqual(['mini', 'mock_ctl'])   // recorded first
    expect(within(rows[0]).getByText('Recorded')).toBeTruthy()
    expect(within(rows[1]).getByText('Mock control')).toBeTruthy()
    expect(rows[0].textContent).toContain('73.3%')            // pass_rate 11/15 from the overview row
    expect(requests).toEqual([])                               // everything came from the primed cache
  })
  it('search and filter narrow the list; an empty result says why and offers a reset', () => {
    const { container } = renderRig('#/rig/home')
    fireEvent.click(screen.getByRole('button', { name: /Mock controls 1/ }))
    expect(container.querySelectorAll('[data-el="dataset-list"] tbody tr')).toHaveLength(1)
    fireEvent.change(screen.getByPlaceholderText('Find a dataset, model or harness'), { target: { value: 'zzz' } })
    expect(screen.getByText('No dataset matches.')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Clear search/ }))
    expect(container.querySelectorAll('[data-el="dataset-list"] tbody tr')).toHaveLength(2)
  })
  it('an empty library offers the ways to fill it', () => {
    renderRig('#/rig/home', { data: { ...rigFixture(), '/overview': { results: [], harnesses: [], tasks: [], jobs: [] } } })
    const empty = document.querySelector('[data-el~="empty-state"]')
    expect(empty.textContent).toContain('No datasets yet.')
    act(() => { fireEvent.click(within(empty).getByRole('button', { name: 'Load trajectories' })) })
    expect(location.hash).toContain('sources')
  })
  it('the library is one stacked table; the mock-control explanation sits behind About', () => {
    const { container } = renderRig('#/rig/home')
    expect(container.querySelector('.rg-home-grid')).toBeNull()
    const about = container.querySelector('details.rg-about')
    expect(about.open).toBe(false)
    expect(about.textContent).toContain('designed failure rates')
  })
  it('opening a dataset adds a ds: tab and writes it to the hash', () => {
    renderRig('#/rig/home')
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Open mini' })) })
    expect(location.hash).toBe('#/rig/home+!ds:mini')
    expect(screen.getByRole('tab', { name: /mini/, selected: true })).toBeTruthy()
  })
})
