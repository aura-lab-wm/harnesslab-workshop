// @vitest-environment jsdom
/* A static export (python -m harnesslab --export lab.html) is the same one UI: the Rig, reading
   from the embedded blob, with no server, no network and no live stream. */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

let App
let requests
beforeAll(async () => {
  HTMLElement.prototype.scrollTo = () => {}
  window.__HARNESSLAB_DATA__ = {
    __meta__: { results: ['my_export'] },
    '/overview': { results: [{ name: 'my_export', runs: 2, models: ['vendor/model'], harnesses: ['baseline'], tasks: ['task'], pass_rate: 0.5, updated: '2026-01-01T00:00:00Z' }], harnesses: [], tasks: [], jobs: [] },
  }
  App = (await import('./App')).default
})
beforeEach(() => {
  localStorage.clear()
  history.replaceState(null, '', '#/')
  requests = []
  vi.stubGlobal('fetch', async (url) => { requests.push(String(url)); throw new Error('No server in static export') })
  vi.stubGlobal('EventSource', class { constructor(url) { requests.push(String(url)) } close() {} })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('static export', () => {
  it('opens the Rig on the exported datasets without touching the network', async () => {
    const { container } = render(<App />)
    expect(location.hash).toBe('#/rig/home')
    await screen.findByRole('heading', { name: 'Datasets', level: 1 })
    expect(screen.getAllByText('my_export').length).toBeGreaterThan(0)
    expect(container.querySelector('[data-el="read-only-export-state"]').textContent).toBe('read-only snapshot')
    expect(requests).toEqual([])
    expect(requests.some((u) => /EventSource|\/events/.test(u))).toBe(false)
  })
})
