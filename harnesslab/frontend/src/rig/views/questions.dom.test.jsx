// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { renderRig, resetRig, rigFixture } from '../../../test/rig-testing'

afterEach(resetRig)

describe('questions', () => {
  it('q:<dir>:<qid> shows the sentence, the number and the figure from live payloads', () => {
    const { container } = renderRig('#/rig/q:mini:grader')
    expect(container.querySelector('h1').textContent).toBe('Can I trust the grader?')
    const ans = container.querySelector('[data-el="answer"]')
    expect(ans.textContent).toContain('6.7 points')
    expect(ans.textContent).toContain('−6.7pp')
    expect(container.querySelector('[data-el="question-figure"]').textContent).toContain('κ 0.87')
    // the breadcrumb IS the evidence chain, Answer current
    const chain = container.querySelector('[data-el="evidence-chain"]')
    expect(within(chain).getByRole('button', { current: 'step' }).textContent).toContain('Answer')
  })
  it('Answer → Runs → Run carries the chain in every spec, deep-linkably', () => {
    const { container } = renderRig('#/rig/q:mini:outcomes')
    act(() => { fireEvent.click(screen.getByRole('button', { name: /Show me the runs/ })) })
    expect(location.hash).toContain('q:mini:outcomes:runs')
    const rows = container.querySelectorAll('[data-el="question-runs"] tbody tr')
    expect(rows).toHaveLength(1)                               // runs whose mode is the top mode
    expect(rows[0].querySelector('.rg-fchip')).toBeTruthy()
    act(() => { fireEvent.click(within(rows[0]).getByRole('button', { name: /^a0000\d$/ })) })
    expect(location.hash).toMatch(/run:20260101-000000-a0000\d~q:mini:outcomes/)
    const chain = container.querySelector('[data-el="evidence-chain"]')
    expect(within(chain).getByRole('button', { current: 'step' }).textContent).toContain('Run')
    expect(within(chain).getByText('Event').closest('.off')).toBeTruthy()   // no event chosen yet
  })
  it('an unanswerable question says why instead of estimating', () => {
    const { container } = renderRig('#/rig/q:mock_ctl:sentinel')
    expect(container.textContent).toContain('Not answered for this dataset.')
    expect(container.textContent).toContain('No sentinel model was trained on mock_ctl')
  })
  it('an unanswered question offers the dataset that CAN answer it, the view that explains, and the way back', () => {
    renderRig('#/rig/q:mock_ctl:sentinel')
    const empty = document.querySelector('[data-el~="question-unanswered"]')
    expect(within(empty).getByRole('button', { name: 'Open Sentinel (trained models)' })).toBeTruthy()
    expect(within(empty).getByRole('button', { name: 'All questions about mock_ctl' })).toBeTruthy()
    act(() => { fireEvent.click(within(empty).getByRole('button', { name: 'Ask it of mini' })) })   // the sentinel was trained on mini
    expect(location.hash).toContain('q:mini:sentinel')
    expect(document.querySelector('[data-el="answer"]')).toBeTruthy()
  })
  it('a probed dataset that cannot answer either is not offered', () => {
    renderRig('#/rig/q:mini:outcomes')          // answerable here; now ask about the one mock_ctl cannot answer
    resetRig()
    const data = { ...rigFixture(), '/results/mock_ctl/oracle': { n_eligible: 0, n_excluded: 2, kappa: null, kappa_undefined_reason: 'no_eligible_pairs', rate_a: {}, rate_b: {} } }
    data['/results/mini/oracle'] = { ...data['/results/mini/oracle'], kappa: null, kappa_undefined_reason: 'no_eligible_pairs' }
    renderRig('#/rig/q:mini:grader', { data })
    const empty = document.querySelector('[data-el~="question-unanswered"]')
    expect(within(empty).queryByRole('button', { name: /Ask it of/ })).toBeNull()
    expect(within(empty).getByRole('button', { name: 'All questions about mini' })).toBeTruthy()
  })
  it('an empty run filter offers to show every run', () => {
    renderRig('#/rig/q:mini:repeats:runs')
    const seg = document.querySelector('[data-el="failure-mode-filter"]')
    fireEvent.click(within(seg).getByRole('button', { name: /^Failed/ }))
    fireEvent.click(within(seg).getByRole('button', { name: /^All/ }))
    expect(document.querySelectorAll('[data-el="question-runs"] tbody tr').length).toBeGreaterThan(0)
  })
  it('the Asking… sentence changes the shared condition', () => {
    const { container } = renderRig('#/rig/q:mini:repeats')
    expect(container.querySelector('h1').textContent).toContain('alpha-1 under baseline')
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'acme/beta-2' } })
    expect(location.hash).toContain('c=mini:acme/beta-2:baseline')
    expect(container.querySelector('h1').textContent).toContain('beta-2 under baseline')
    expect(document.querySelector('.rg-sb').textContent).toContain('beta-2 · baseline')
  })
  it('pins the answer and the figure to the case file', () => {
    renderRig('#/rig/q:mini:leak')
    fireEvent.click(screen.getByRole('button', { name: /^Pin Is anything leaking/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Pin Figure/ }))
    const saved = JSON.parse(localStorage.getItem('rig.case'))
    expect(saved.map((x) => x.kind)).toEqual(['answer', 'figure'])
    expect(saved[0].value).toContain('40%')
  })
  it('unknown question id is not found', () => {
    const { container } = renderRig('#/rig/q:mini:nope')
    expect(container.querySelector('[data-el="not-found"]')).toBeTruthy()
  })
})
