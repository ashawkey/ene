import { useEffect, useRef, useState } from 'react'

export type LinkStatus = 'connecting' | 'open' | 'reconnecting' | 'offline' | 'closed'

export type SocketOptions = {
  url: () => string
  onMessage: (message: Record<string, unknown>) => void
  onOpen?: () => void
  /** Called for close codes that must not be retried (auth loss, session gone). */
  onFatal?: (code: number) => void
  fatalCodes?: number[]
}

// Backoff grows to a ceiling and is jittered so many tabs do not reconnect in
// lockstep after a hub restart.
const BACKOFF = [400, 1000, 2000, 4000, 8000, 12000]
const CONNECT_TIMEOUT = 10_000
const PING_INTERVAL = 15_000
const PONG_TIMEOUT = 10_000

export function backoffDelay(attempt: number, random: () => number = Math.random) {
  const base = BACKOFF[Math.min(attempt, BACKOFF.length - 1)]
  return Math.round(base * (0.75 + random() * 0.5))
}

/**
 * A WebSocket that keeps itself alive: heartbeats detect half-open links
 * (common on mobile networks and tunnels), drops reconnect with backoff, and
 * coming back online or to the foreground re-checks immediately.
 */
export class ResilientSocket {
  status: LinkStatus = 'connecting'
  private ws: WebSocket | null = null
  private attempt = 0
  private timers: { retry?: number; connect?: number; ping?: number; pong?: number } = {}
  private disposed = false
  private everOpen = false
  private listeners = new Set<(status: LinkStatus) => void>()
  private options: SocketOptions

  constructor(options: SocketOptions) {
    this.options = options
    window.addEventListener('online', this.check)
    window.addEventListener('offline', this.wentOffline)
    document.addEventListener('visibilitychange', this.onVisibility)
    this.connect()
  }

  update(options: SocketOptions) {
    this.options = options
  }

  subscribe(listener: (status: LinkStatus) => void) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private setStatus(status: LinkStatus) {
    if (this.status === status) return
    this.status = status
    for (const listener of this.listeners) listener(status)
  }

  private clear(...names: Array<keyof ResilientSocket['timers']>) {
    for (const name of names) {
      const id = this.timers[name]
      if (id !== undefined) {
        window.clearTimeout(id)
        window.clearInterval(id)
      }
      this.timers[name] = undefined
    }
  }

  private connect = () => {
    if (this.disposed) return
    this.clear('retry', 'connect', 'ping', 'pong')
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      this.setStatus('offline')
      return
    }
    this.setStatus(this.everOpen ? 'reconnecting' : 'connecting')
    let ws: WebSocket
    try {
      ws = new WebSocket(this.options.url())
    } catch {
      this.scheduleRetry()
      return
    }
    this.ws = ws
    this.timers.connect = window.setTimeout(() => this.drop(ws), CONNECT_TIMEOUT)
    ws.onopen = () => {
      if (this.ws !== ws) return
      this.clear('connect')
      this.timers.ping = window.setInterval(() => this.ping(ws), PING_INTERVAL)
    }
    ws.onmessage = (event) => {
      if (this.ws !== ws) return
      let message: unknown
      try {
        message = JSON.parse(String(event.data))
      } catch {
        return
      }
      if (!message || typeof message !== 'object') return
      this.clear('pong')
      if (this.status !== 'open') {
        // The first frame proves authentication and liveness end to end.
        this.attempt = 0
        this.everOpen = true
        this.setStatus('open')
        this.options.onOpen?.()
      }
      if ((message as { type?: string }).type === 'pong') return
      this.options.onMessage(message as Record<string, unknown>)
    }
    ws.onclose = (event) => {
      if (this.ws !== ws) return
      this.ws = null
      this.clear('connect', 'ping', 'pong')
      if ((this.options.fatalCodes ?? [4403]).includes(event.code)) {
        this.setStatus('closed')
        this.options.onFatal?.(event.code)
        return
      }
      this.scheduleRetry()
    }
  }

  private scheduleRetry() {
    if (this.disposed) return
    this.setStatus(navigator.onLine === false ? 'offline' : 'reconnecting')
    if (navigator.onLine === false) return
    this.clear('retry')
    this.timers.retry = window.setTimeout(this.connect, backoffDelay(this.attempt++))
  }

  private drop(ws: WebSocket) {
    if (this.ws !== ws) return
    this.ws = null
    this.clear('connect', 'ping', 'pong')
    try {
      ws.close()
    } catch {
      // Already closing.
    }
    this.scheduleRetry()
  }

  private ping(ws: WebSocket) {
    if (this.ws !== ws || ws.readyState !== WebSocket.OPEN) return
    try {
      ws.send('{"type":"ping"}')
    } catch {
      this.drop(ws)
      return
    }
    if (this.timers.pong === undefined) {
      this.timers.pong = window.setTimeout(() => {
        this.timers.pong = undefined
        // Hidden tabs throttle timers; verify when visible instead.
        if (document.visibilityState === 'hidden') return
        this.drop(ws)
      }, PONG_TIMEOUT)
    }
  }

  /** Probe now: ping an open link, or reconnect immediately if down. */
  check = () => {
    if (this.disposed || this.status === 'closed') return
    const ws = this.ws
    if (ws && ws.readyState === WebSocket.OPEN) {
      this.ping(ws)
    } else if (!ws || ws.readyState !== WebSocket.CONNECTING) {
      this.attempt = 0
      this.connect()
    }
  }

  private wentOffline = () => {
    if (this.status !== 'closed') this.setStatus('offline')
  }

  private onVisibility = () => {
    if (document.visibilityState === 'visible') this.check()
  }

  /** Send a JSON frame; false when the link is not open (nothing was sent). */
  send(message: unknown): boolean {
    const ws = this.ws
    if (this.status !== 'open' || !ws || ws.readyState !== WebSocket.OPEN) return false
    try {
      ws.send(JSON.stringify(message))
      return true
    } catch {
      this.drop(ws)
      return false
    }
  }

  retry() {
    this.status = this.status === 'closed' ? 'reconnecting' : this.status
    this.attempt = 0
    this.connect()
  }

  dispose() {
    this.disposed = true
    this.clear('retry', 'connect', 'ping', 'pong')
    window.removeEventListener('online', this.check)
    window.removeEventListener('offline', this.wentOffline)
    document.removeEventListener('visibilitychange', this.onVisibility)
    const ws = this.ws
    this.ws = null
    try {
      ws?.close()
    } catch {
      // Ignore.
    }
    this.listeners.clear()
  }
}

/** React binding: one socket for the component's lifetime (while enabled). */
export function useSocket(options: SocketOptions & { enabled?: boolean }) {
  const { enabled = true } = options
  const [status, setStatus] = useState<LinkStatus>('connecting')
  const socket = useRef<ResilientSocket | null>(null)
  const latest = useRef(options)
  latest.current = options

  useEffect(() => {
    if (!enabled) return
    const instance = new ResilientSocket({
      url: () => latest.current.url(),
      onMessage: (message) => latest.current.onMessage(message),
      onOpen: () => latest.current.onOpen?.(),
      onFatal: (code) => latest.current.onFatal?.(code),
      fatalCodes: latest.current.fatalCodes,
    })
    socket.current = instance
    setStatus(instance.status)
    const unsubscribe = instance.subscribe(setStatus)
    return () => {
      unsubscribe()
      instance.dispose()
      if (socket.current === instance) socket.current = null
    }
  }, [enabled])

  return {
    status,
    socket,
    send: (message: unknown) => socket.current?.send(message) ?? false,
    retry: () => socket.current?.retry(),
  }
}
