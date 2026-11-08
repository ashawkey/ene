import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSession } from './useSession'

class FakeSocket {
  static all: FakeSocket[] = []
  static OPEN = 1
  static CONNECTING = 0
  readyState = 0
  sent: Array<Record<string, unknown>> = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeSocket.all.push(this)
  }

  send(data: string) {
    this.sent.push(JSON.parse(data))
  }

  close() {
    this.readyState = 3
  }

  open() {
    this.readyState = 1
    this.onopen?.()
  }

  push(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }

  drop() {
    this.readyState = 3
    this.onclose?.({ code: 1006 })
  }
}

const stateFrame = (pending: Record<string, unknown> | null = null) => ({
  type: 'state', csrf: 'c', session: 's1', stream_id: 'x', latest_seq: 0, oldest_seq: 0,
  replay_truncated: false, operation_id: null, process_status: '', context_status: null,
  active_indicator: null, commands: {}, prompt: null, pending,
})

const latest = () => FakeSocket.all.at(-1)!
const submits = (socket: FakeSocket) => socket.sent.filter((message) => message.type === 'submit')

describe('useSession outbox', () => {
  beforeEach(() => {
    FakeSocket.all = []
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeSocket)
    window.localStorage.clear()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  function connect() {
    act(() => {
      latest().open()
      latest().push(stateFrame())
    })
  }

  it('re-sends an unacknowledged submit with the same action id after reconnecting', () => {
    const { result } = renderHook(() => useSession('s1', () => undefined))
    connect()
    let ok = false
    act(() => { ok = result.current.submit('hello') })
    expect(ok).toBe(true)
    const [first] = submits(latest())
    expect(result.current.outbox).toHaveLength(1)

    act(() => latest().drop())
    act(() => { vi.advanceTimersByTime(1000) })
    expect(FakeSocket.all).toHaveLength(2)
    connect()
    const [resent] = submits(latest())
    expect(resent).toEqual(first)

    act(() => latest().push({ type: 'accepted', action_id: first.action_id, duplicate: true }))
    expect(result.current.outbox).toHaveLength(0)
  })

  it('treats a pending message in the new state frame as delivery', () => {
    const { result } = renderHook(() => useSession('s1', () => undefined))
    connect()
    act(() => { result.current.submit('hello') })
    const [first] = submits(latest())
    act(() => latest().drop())
    act(() => { vi.advanceTimersByTime(1000) })
    act(() => {
      latest().open()
      latest().push(stateFrame({ id: 'p', text: 'hello', source: 'web', action_id: first.action_id }))
    })
    expect(submits(latest())).toHaveLength(0)
    expect(result.current.outbox).toHaveLength(0)
  })

  it('restores the text to the composer when the agent rejects it', () => {
    const { result } = renderHook(() => useSession('s1', () => undefined))
    connect()
    act(() => { result.current.submit('/busy command') })
    const [first] = submits(latest())
    act(() => latest().push({ type: 'accepted', action_id: first.action_id }))
    expect(result.current.draft).toBe('')
    act(() => latest().push({ seq: 1, type: 'submission_rejected', data: { action_id: first.action_id, error: 'pending' } }))
    expect(result.current.draft).toBe('/busy command')
  })

  it('refuses to send while disconnected and ignores a double send', () => {
    const { result } = renderHook(() => useSession('s1', () => undefined))
    let ok = true
    act(() => { ok = result.current.submit('early') })
    expect(ok).toBe(false)
    connect()
    act(() => { result.current.submit('once') })
    act(() => { ok = result.current.submit('once') })
    expect(ok).toBe(false)
    expect(submits(latest())).toHaveLength(1)
  })

  it('renders streaming deltas once per frame and flushes them with the next event', () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback))
    vi.stubGlobal('cancelAnimationFrame', () => undefined)
    const { result } = renderHook(() => useSession('s1', () => undefined))
    connect()
    const text = () => result.current.view.items.map((item) => ('text' in item ? item.text : '')).join('|')
    act(() => {
      latest().push({ type: 'assistant_delta', seq: 1, data: { text: 'Hel' } })
      latest().push({ type: 'assistant_delta', seq: 2, data: { text: 'lo' } })
    })
    expect(text()).toBe('')
    expect(frames).toHaveLength(1)
    act(() => frames.shift()!(0))
    expect(text()).toBe('Hello')

    act(() => {
      latest().push({ type: 'assistant_delta', seq: 3, data: { text: '!' } })
      latest().push({ type: 'assistant_message', seq: 4, data: { text: 'Hello!' } })
    })
    expect(text()).toBe('Hello!')
  })

  it('holds renders while hidden and catches up when shown', () => {
    const { result, rerender } = renderHook(({ visible }) => useSession('s1', () => undefined, visible), {
      initialProps: { visible: false },
    })
    connect()
    act(() => latest().push({ type: 'user_message', seq: 1, data: { text: 'hi', source: 'web' } }))
    expect(result.current.view.items).toHaveLength(0)
    rerender({ visible: true })
    expect(result.current.view.items).toHaveLength(1)
  })
})
