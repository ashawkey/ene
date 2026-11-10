import { backoffDelay } from './socket'

export type TerminalLinkStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'

export type TerminalSocketOptions = {
  url: string
  /** Raw shell output; each connection starts with the scrollback replay. */
  onData: (data: Uint8Array) => void
  /** Called before a reconnect replays the scrollback, so the view can reset. */
  onReconnect: () => void
  onExit: (code: number | null) => void
  onStatus: (status: TerminalLinkStatus) => void
}

const PING_INTERVAL = 15_000
// Large pastes go out in pieces the hub accepts (it drops frames over 64 KiB).
export const INPUT_CHUNK = 32 * 1024
// Closed for good: login gone (4403) or the terminal no longer exists (4404).
const FATAL = new Set([4403, 4404])

export function chunkBytes(data: Uint8Array, size = INPUT_CHUNK): Uint8Array[] {
  const chunks: Uint8Array[] = []
  for (let start = 0; start < data.length; start += size) chunks.push(data.subarray(start, start + size))
  return chunks
}

/**
 * The binary relay to one hub terminal. Reconnects with backoff; every
 * connection starts with the hub replaying the terminal's scrollback.
 */
export class TerminalSocket {
  private ws: WebSocket | null = null
  private attempt = 0
  private everOpen = false
  private disposed = false
  private exited = false
  private retry?: number
  private ping?: number
  private size: { cols: number; rows: number } | null = null
  private encoder = new TextEncoder()

  constructor(private options: TerminalSocketOptions) {
    this.connect()
  }

  private connect = () => {
    if (this.disposed || this.exited) return
    this.options.onStatus(this.everOpen ? 'reconnecting' : 'connecting')
    let ws: WebSocket
    try {
      ws = new WebSocket(this.options.url)
    } catch {
      this.scheduleRetry()
      return
    }
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      if (this.ws !== ws) return
      if (this.everOpen) this.options.onReconnect()
      this.everOpen = true
      this.attempt = 0
      this.options.onStatus('open')
      if (this.size) this.sendJson({ type: 'resize', ...this.size })
      this.ping = window.setInterval(() => this.sendJson({ type: 'ping' }), PING_INTERVAL)
    }
    ws.onmessage = (event) => {
      if (this.ws !== ws) return
      if (event.data instanceof ArrayBuffer) {
        this.options.onData(new Uint8Array(event.data))
        return
      }
      let message: { type?: string; code?: number | null }
      try {
        message = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (message?.type === 'exit') {
        this.exited = true
        this.options.onExit(typeof message.code === 'number' ? message.code : null)
      }
    }
    ws.onclose = (event) => {
      if (this.ws !== ws) return
      this.ws = null
      window.clearInterval(this.ping)
      if (this.exited || FATAL.has(event.code)) {
        this.options.onStatus('closed')
        return
      }
      this.scheduleRetry()
    }
  }

  private scheduleRetry() {
    if (this.disposed) return
    this.options.onStatus('reconnecting')
    window.clearTimeout(this.retry)
    this.retry = window.setTimeout(this.connect, backoffDelay(this.attempt++))
  }

  private sendJson(message: unknown) {
    const ws = this.ws
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message))
  }

  /** Keyboard input or a paste; dropped while disconnected. */
  input(data: string | Uint8Array) {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN || this.exited) return
    const bytes = typeof data === 'string' ? this.encoder.encode(data) : data
    for (const chunk of chunkBytes(bytes)) ws.send(chunk)
  }

  resize(cols: number, rows: number) {
    if (this.size?.cols === cols && this.size?.rows === rows) return
    this.size = { cols, rows }
    this.sendJson({ type: 'resize', cols, rows })
  }

  dispose() {
    this.disposed = true
    window.clearTimeout(this.retry)
    window.clearInterval(this.ping)
    const ws = this.ws
    this.ws = null
    ws?.close()
  }
}

/** True for keyboard events aimed at a terminal, which must not trigger page shortcuts. */
export const inTerminal = (target: EventTarget | null) =>
  target instanceof Element && target.closest('.xterm') !== null
