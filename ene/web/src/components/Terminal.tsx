import { FitAddon } from '@xterm/addon-fit'
import { Terminal as XTerm, type ITheme } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { Plus, RotateCcw, SquareTerminal, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { closeTerminal, createTerminal, errorMessage, listTerminals, terminalSocketUrl } from '../lib/api'
import type { TerminalInfo } from '../lib/protocol'
import { store } from '../lib/storage'
import { TerminalSocket, type TerminalLinkStatus } from '../lib/terminalSocket'
import { toast } from '../lib/toast'
import { Empty, IconButton, Spinner } from './ui'

const REFRESH_INTERVAL = 5000

const LIGHT: ITheme = {
  background: '#fbfcfe',
  foreground: '#1d2330',
  cursor: '#0a7aff',
  cursorAccent: '#fbfcfe',
  selectionBackground: 'rgba(10, 122, 255, 0.22)',
  black: '#1d2330', red: '#c62f32', green: '#1f8a4c', yellow: '#a86a00',
  blue: '#0a63d1', magenta: '#8e44c9', cyan: '#0f8790', white: '#c8ccd4',
  brightBlack: '#6b7280', brightRed: '#e5383b', brightGreen: '#25a35a', brightYellow: '#c98a00',
  brightBlue: '#2f86ff', brightMagenta: '#a55ee0', brightCyan: '#16a3ad', brightWhite: '#f3f4f6',
}

const DARK: ITheme = {
  background: '#11141b',
  foreground: '#dfe3ec',
  cursor: '#5aa2ff',
  cursorAccent: '#11141b',
  selectionBackground: 'rgba(90, 162, 255, 0.3)',
  black: '#1b1f29', red: '#f0686a', green: '#4cc583', yellow: '#e5b45a',
  blue: '#5aa2ff', magenta: '#c08cf5', cyan: '#4ec6cf', white: '#c9ceda',
  brightBlack: '#6b7385', brightRed: '#ff8587', brightGreen: '#6fd99c', brightYellow: '#f2c874',
  brightBlue: '#7db6ff', brightMagenta: '#d2a8ff', brightCyan: '#72d8e0', brightWhite: '#ffffff',
}

const terminalTheme = () =>
  getComputedStyle(document.documentElement).colorScheme.includes('dark') ? DARK : LIGHT

/** Re-run *apply* when the page theme (explicit choice or system) changes. */
function watchTheme(apply: () => void) {
  const observer = new MutationObserver(apply)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  const media = window.matchMedia?.('(prefers-color-scheme: dark)')
  media?.addEventListener?.('change', apply)
  return () => {
    observer.disconnect()
    media?.removeEventListener?.('change', apply)
  }
}

const binaryString = (data: string) => Uint8Array.from(data, (char) => char.charCodeAt(0) & 0xff)

function TerminalView({
  sessionId,
  terminal,
  onExit,
}: {
  sessionId: string
  terminal: TerminalInfo
  onExit: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<TerminalLinkStatus>('connecting')
  const [exitCode, setExitCode] = useState<number | null | undefined>(terminal.alive ? undefined : terminal.exit_code)
  const exitRef = useRef(onExit)
  exitRef.current = onExit

  useEffect(() => {
    const element = host.current
    if (!element) return
    const term = new XTerm({
      allowProposedApi: false,
      convertEol: false,
      cursorBlink: true,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace',
      fontSize: 12.5,
      lineHeight: 1.15,
      scrollback: 5000,
      theme: terminalTheme(),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(element)
    const socket = new TerminalSocket({
      url: terminalSocketUrl(sessionId, terminal.id),
      onData: (data) => term.write(data),
      onReconnect: () => term.reset(),
      onExit: (code) => {
        setExitCode(code)
        term.write(`\r\n\x1b[2m[process exited${code === null ? '' : ` with code ${code}`}]\x1b[0m\r\n`)
        exitRef.current()
      },
      onStatus: setStatus,
    })
    const refit = () => {
      if (!element.offsetParent) return // hidden: keep the last size
      try {
        fit.fit()
      } catch {
        return
      }
      socket.resize(term.cols, term.rows)
    }
    let frame = 0
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(refit)
    })
    observer.observe(element)
    refit()
    // Glyph metrics can change once the monospace font finishes loading.
    document.fonts?.ready.then(() => requestAnimationFrame(refit)).catch(() => {})
    const data = term.onData((input) => socket.input(input))
    const binary = term.onBinary((input) => socket.input(binaryString(input)))
    const stopTheme = watchTheme(() => {
      term.options.theme = terminalTheme()
    })
    term.focus()
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      stopTheme()
      data.dispose()
      binary.dispose()
      socket.dispose()
      term.dispose()
    }
  }, [sessionId, terminal.id])

  return (
    <div className="term-view">
      <div className="term-host" ref={host} />
      {status === 'reconnecting' && exitCode === undefined ? (
        <div className="term-status"><Spinner size={12} /> Reconnecting…</div>
      ) : null}
    </div>
  )
}

export function TerminalPanel({ sessionId, connected }: { sessionId: string; connected: boolean }) {
  const activeKey = `ene.terminal.${sessionId}`
  const [terminals, setTerminals] = useState<TerminalInfo[] | null>(null)
  const [supported, setSupported] = useState(true)
  const [activeId, setActiveState] = useState<string | null>(() => store.get(activeKey))
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  const setActive = useCallback((id: string | null) => {
    setActiveState(id)
    store.set(activeKey, id)
  }, [activeKey])

  const refresh = useCallback(() => {
    listTerminals(sessionId).then(
      (listing) => {
        setSupported(listing.supported)
        setTerminals(listing.terminals)
        setError('')
      },
      (exc) => setError(errorMessage(exc, 'Could not list terminals.')),
    )
  }, [sessionId])

  useEffect(() => {
    refresh()
    // Other tabs may open or close terminals; keep the list roughly current.
    const timer = window.setInterval(refresh, REFRESH_INTERVAL)
    return () => window.clearInterval(timer)
  }, [refresh])

  const create = () => {
    if (creating) return
    setCreating(true)
    createTerminal(sessionId, { cols: 80, rows: 24 })
      .then((terminal) => {
        setTerminals((current) => [...(current ?? []), terminal])
        setActive(terminal.id)
      })
      .catch((exc) => toast(errorMessage(exc, 'Could not open a terminal.')))
      .finally(() => setCreating(false))
  }

  const close = (terminal: TerminalInfo) => {
    const list = terminals ?? []
    const index = list.findIndex((item) => item.id === terminal.id)
    const rest = list.filter((item) => item.id !== terminal.id)
    setTerminals(rest)
    if (activeId === terminal.id) setActive(rest[Math.min(index, rest.length - 1)]?.id ?? null)
    closeTerminal(sessionId, terminal.id)
      .catch((exc) => toast(errorMessage(exc, 'Could not close the terminal.')))
      .finally(refresh)
  }

  if (!supported) {
    return <Empty icon={SquareTerminal}>Terminals are not supported on this hub's platform.</Empty>
  }
  const list = terminals ?? []
  const active = list.find((item) => item.id === activeId) ?? list.at(-1) ?? null
  return (
    <div className="terminals">
      <div className="term-tabs" role="tablist" aria-label="Terminals">
        {list.map((terminal) => (
          <div
            key={terminal.id}
            className={`term-tab ${terminal.id === active?.id ? 'active' : ''} ${terminal.alive ? '' : 'exited'}`}
          >
            <button
              type="button"
              role="tab"
              aria-selected={terminal.id === active?.id}
              onClick={() => setActive(terminal.id)}
              data-tip={terminal.alive ? terminal.shell : `Exited${terminal.exit_code === null ? '' : ` with code ${terminal.exit_code}`}`}
            >
              {terminal.title}
            </button>
            <IconButton icon={X} size="sm" label={`Close ${terminal.title}`} onClick={() => close(terminal)} />
          </div>
        ))}
        <IconButton icon={Plus} size="sm" label="New terminal" busy={creating} disabled={!connected} onClick={create} />
      </div>
      {error ? <div className="panel-error">{error}</div> : null}
      {terminals === null && !error ? (
        <div className="empty"><Spinner /></div>
      ) : active ? (
        <>
          <TerminalView key={active.id} sessionId={sessionId} terminal={active} onExit={refresh} />
          {active.alive ? null : (
            <div className="term-exited">
              <span className="muted small">
                Exited{active.exit_code === null ? '' : ` with code ${active.exit_code}`}
              </span>
              <button type="button" className="btn" disabled={!connected || creating} onClick={() => { close(active); create() }}>
                <RotateCcw size={13} /> Restart
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="empty">
          <SquareTerminal size={28} strokeWidth={1.4} />
          <div>A shell in the session workspace.</div>
          <button type="button" className="btn" disabled={!connected || creating} onClick={create}>
            <Plus size={13} /> New terminal
          </button>
        </div>
      )}
    </div>
  )
}
