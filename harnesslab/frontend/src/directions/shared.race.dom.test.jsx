// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { Evidence } from './shared'

const json = data => ({ ok: true, headers: { get: () => 'application/json' }, json: async () => data })
const recording = task => ({
  summary: { run_id: task, task_id: task, model: 'model', harness_id: 'baseline', hidden_pass: true, visible_pass: true, input_tokens: 10, output_tokens: 5, cost_usd: 0, exit_reason: 'submitted' },
  spans: [{ seq: 0, span: 'chat', text: `Evidence for ${task}` }], patch: '', issue: task,
})
let previous, resolvePrevious, rejectPrevious, requests
beforeEach(() => {
  requests = []
  previous = new Promise((resolve, reject) => { resolvePrevious = resolve; rejectPrevious = reject })
  vi.stubGlobal('fetch', async url => {
    requests.push(url)
    if (url === '/api/results/first/runs/old') return previous
    if (url === '/api/results/first/runs/current' || url === '/api/results/second/runs/old') return json(recording('Current task'))
    throw new Error(`Unexpected request: ${url}`)
  })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('shared evidence request ownership', () => {
  it('ignores a slow previous run after a different run has loaded', async () => {
    const view = render(<Evidence dataset="first" runId="old" />)
    expect(screen.getByRole('status').textContent).toContain('Reading the recorded trajectory')
    view.rerender(<Evidence dataset="first" runId="current" />)
    const current = await screen.findByRole('article', { name: 'Run evidence' })
    expect(within(current).getByRole('heading', { name: 'Current task' })).toBeTruthy()
    await act(async () => { resolvePrevious(json(recording('Previous task'))) })
    expect(screen.getByRole('heading', { name: 'Current task' })).toBeTruthy()
    expect(screen.queryByText('Evidence for Previous task')).toBeNull()
    expect(requests).toEqual(['/api/results/first/runs/old', '/api/results/first/runs/current'])
  })

  it('ignores a previous run failure after the new evidence has loaded', async () => {
    const view = render(<Evidence dataset="first" runId="old" />)
    view.rerender(<Evidence dataset="first" runId="current" />)
    await screen.findByRole('heading', { name: 'Current task' })
    await act(async () => { rejectPrevious(new Error('Previous request failed')) })
    expect(screen.getByRole('heading', { name: 'Current task' })).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('treats the same run ID in another dataset as a different recording', async () => {
    const view = render(<Evidence dataset="first" runId="old" />)
    view.rerender(<Evidence dataset="second" runId="old" />)
    await screen.findByRole('heading', { name: 'Current task' })
    await act(async () => { resolvePrevious(json(recording('Previous dataset task'))) })
    expect(screen.getByRole('heading', { name: 'Current task' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Previous dataset task' })).toBeNull()
  })
})
