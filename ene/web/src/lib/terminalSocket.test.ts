import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { chunkBytes, inTerminal, TerminalSocket } from './terminalSocket'

class FakeSocket {
  static instances: FakeSocket[] = []
  static OPEN = 1
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen?: () => void
  onmessage?: (event: { data: unknown }) => void
  onclose?: (event: { code: number }) => void
  constructor(public url: string) {
    FakeSocket.instances.push(this)
  }
  send(data: unknown) {
    this.sent.push(data)
  }
  close() {
    this.readyState = 3
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
}

beforeEach(() => {
  FakeSocket.instances = []
  vi.useFakeTimers()
  vi.stubGlobal('WebSocket', FakeSocket)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function connect() {
  const events = { data: [] as Uint8Array[], exits: [] as Array<number | null>, statuses: [] as string[], resets: 0 }
  const socket = new TerminalSocket({
    url: 'ws://hub/t',
    onData: (data) => events.data.push(data),
    onReconnect: () => { events.resets += 1 },
    onExit: (code) => events.exits.push(code),
    onStatus: (status) => events.statuses.push(status),
  })
  return { socket, events, ws: () => FakeSocket.instances.at(-1)! }
}

describe('terminal socket', () => {
  it('relays binary output, input, and the latest size', () => {
    const { socket, events, ws } = connect()
    socket.resize(100, 30)
    ws().open()
    expect(ws().binaryType).toBe('arraybuffer')
    expect(ws().sent).toEqual([JSON.stringify({ type: 'resize', cols: 100, rows: 30 })])
    ws().onmessage?.({ data: new Uint8Array([104, 105]).buffer })
    expect(Array.from(events.data[0])).toEqual([104, 105])
    socket.input('ls\r')
    expect(Array.from(ws().sent.at(-1) as Uint8Array)).toEqual([108, 115, 13])
    expect(events.statuses).toEqual(['connecting', 'open'])
  })

  it('reconnects after a drop and resets the view before the replay', () => {
    const { events, ws } = connect()
    ws().open()
    ws().onclose?.({ code: 1006 })
    expect(events.statuses.at(-1)).toBe('reconnecting')
    vi.advanceTimersByTime(1000)
    expect(FakeSocket.instances).toHaveLength(2)
    ws().open()
    expect(events.resets).toBe(1)
  })

  it('stops after the shell exits or the terminal is gone', () => {
    const { events, ws } = connect()
    ws().open()
    ws().onmessage?.({ data: JSON.stringify({ type: 'exit', code: 2 }) })
    ws().onclose?.({ code: 1000 })
    vi.advanceTimersByTime(20_000)
    expect(events.exits).toEqual([2])
    expect(FakeSocket.instances).toHaveLength(1)
    expect(events.statuses.at(-1)).toBe('closed')

    const second = connect()
    second.ws().onclose?.({ code: 4404 })
    vi.advanceTimersByTime(20_000)
    expect(FakeSocket.instances).toHaveLength(2)
  })

  it('splits large pastes into frames the hub accepts', () => {
    const chunks = chunkBytes(new Uint8Array(70_000))
    expect(chunks.map((chunk) => chunk.length)).toEqual([32768, 32768, 4464])
  })

  it('recognizes events aimed at a terminal', () => {
    document.body.innerHTML = '<div class="xterm"><textarea></textarea></div><input />'
    expect(inTerminal(document.querySelector('textarea'))).toBe(true)
    expect(inTerminal(document.querySelector('input'))).toBe(false)
    expect(inTerminal(null)).toBe(false)
  })
})
