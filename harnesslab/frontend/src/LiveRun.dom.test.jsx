// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { useLiveRun } from './api'

/* A fake EventSource the tests drive by hand: emit() dispatches a message or a named event,
   open()/fail() play the browser's readyState semantics -- CLOSED (2) after a non-200 response
   (no retry), CONNECTING (0) after a dropped connection (retry with Last-Event-ID). */
class FakeES {
  static instances = []
  constructor(url) { this.url = url; this.readyState = 0; this.listeners = {}; this.closed = false; FakeES.instances.push(this) }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn) }
  close() { this.closed = true; this.readyState = 2 }
  emit(type, data) {
    const ev = { data: typeof data === 'string' ? data : JSON.stringify(data) }
    if (type === 'message' && this.onmessage) this.onmessage(ev)
    for (const fn of this.listeners[type] || []) fn(ev)
  }
  open() { this.readyState = 1; this.onopen && this.onopen({}) }
  fail(closed) { this.readyState = closed ? 2 : 0; this.onerror && this.onerror({}) }
}

function Probe({ dir, runId, enabled = true }) {
  const s = useLiveRun(dir, runId, enabled)
  return <pre data-testid="s">{JSON.stringify({ phase: s.phase, n: s.spans.length, last: s.lastSeq, un: s.unreadable, rc: s.reconnecting, sum: s.summary && s.summary.exit_reason })}</pre>
}
const state = () => JSON.parse(screen.getByTestId('s').textContent)
const span = (seq, span = 'chat') => ({ seq, span, run_id: 'r1' })

beforeEach(() => { FakeES.instances = []; vi.stubGlobal('EventSource', FakeES) })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('useLiveRun', () => {
  it('opens the stream for the run and appends spans in order, ignoring a replayed seq', () => {
    render(<Probe dir="demo mock" runId="r1" />)
    const es = FakeES.instances[0]
    expect(es.url).toBe('/api/runs/demo%20mock/r1/stream')
    expect(state().phase).toBe('connecting')
    act(() => { es.open(); es.emit('message', span(0, 'invoke_agent')); es.emit('message', span(1)); es.emit('message', span(1)) })
    expect(state()).toMatchObject({ phase: 'live', n: 2, last: 1 })
  })

  it('finishes on the end event with its summary and closes the source', () => {
    render(<Probe dir="d" runId="r1" />)
    const es = FakeES.instances[0]
    act(() => { es.open(); es.emit('message', span(0)); es.emit('end', { exit_reason: 'submitted' }) })
    expect(state()).toMatchObject({ phase: 'finished', n: 1, sum: 'submitted' })
    expect(es.closed).toBe(true)
  })

  it('an end event with a null body still finishes (the page then reads run_detail)', () => {
    render(<Probe dir="d" runId="r1" />)
    act(() => { FakeES.instances[0].emit('end', 'null') })
    expect(state()).toMatchObject({ phase: 'finished', sum: null })
  })

  it('marks the run abandoned on the synthetic status and closes', () => {
    render(<Probe dir="d" runId="r1" />)
    const es = FakeES.instances[0]
    act(() => { es.open(); es.emit('message', { span: 'live_status', status: 'abandoned' }) })
    expect(state().phase).toBe('abandoned')
    expect(es.closed).toBe(true)
  })

  it('a closed connection (non-200) means the run is missing; a dropped one means reconnecting', () => {
    render(<Probe dir="d" runId="r1" />)
    const es = FakeES.instances[0]
    act(() => es.fail(false))
    expect(state()).toMatchObject({ rc: true })
    act(() => es.open())
    expect(state()).toMatchObject({ rc: false })
    act(() => es.fail(true))
    expect(state().phase).toBe('missing')
  })

  it('counts unreadable lines once per line number, even when a reconnect replays them', () => {
    render(<Probe dir="d" runId="r1" />)
    const es = FakeES.instances[0]
    act(() => { es.emit('message', { span: 'live_status', status: 'unreadable_line', line: 2 }); es.emit('message', { span: 'live_status', status: 'unreadable_line', line: 2 }); es.emit('message', { span: 'live_status', status: 'unreadable_line', line: 9 }) })
    expect(state().un).toBe(2)
  })

  it('opens nothing when disabled, and reopens for a new run id', () => {
    const { rerender } = render(<Probe dir="d" runId="r1" enabled={false} />)
    expect(FakeES.instances).toHaveLength(0)
    rerender(<Probe dir="d" runId="r1" />)
    expect(FakeES.instances).toHaveLength(1)
    act(() => FakeES.instances[0].emit('message', span(0)))
    rerender(<Probe dir="d" runId="r2" />)
    expect(FakeES.instances).toHaveLength(2)
    expect(FakeES.instances[0].closed).toBe(true)
    expect(state()).toMatchObject({ phase: 'connecting', n: 0 })
  })
})
