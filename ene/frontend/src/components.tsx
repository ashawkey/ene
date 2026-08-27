import { FormEvent, KeyboardEvent, ReactNode, useEffect, useRef, useState } from 'react'

import type { ConnectionStatus } from './connection'
import type { PendingMessage, ProcessStatus, Prompt, SessionSummary } from './types'
import type { Theme } from './theme'

export function ConnectionBanner({
  status,
  onRetry,
  initial = false,
}: {
  status: ConnectionStatus
  onRetry: () => void
  initial?: boolean
}) {
  if (status === 'connected') return null
  const message = status === 'connecting' ? 'Connecting to ene…' : 'Connection lost. Reconnecting…'
  return (
    <div className={`connection-banner${initial ? ' initial' : ''}`} role="status">
      <span className="connection-dot" aria-hidden="true" />
      <span>{message}</span>
      {status === 'reconnecting' ? <button type="button" onClick={onRetry}>Retry now</button> : null}
    </div>
  )
}

function sessionDir(session: SessionSummary) {
  return session.cwd
    ? session.cwd.split(/[\\/]/).filter(Boolean).pop() || session.cwd
    : session.title
}

/**
 * Primary line of a session card: its name, else the last thing asked of it.
 * A session with neither is brand new; the directory is already the second
 * line, so repeating it here would say the same thing twice.
 */
function sessionLabel(session: SessionSummary) {
  return session.name || session.preview || 'New session'
}

export function sessionStatus(session: SessionSummary) {
  if (session.attached_by === 'terminal') {
    return { kind: 'terminal', title: 'Attached in a terminal' }
  }
  const titles: Record<string, string> = {
    working: 'Working',
    waiting: 'Waiting on queued input',
    done: 'Done · needs review',
  }
  // Finished work is green in an open tab (review it here) and yellow when
  // detached (it needs attaching first), so the rail distinguishes review you
  // can act on now from review that is one click away.
  const kind =
    session.state === 'done' && session.attached_by !== 'web' ? 'review' : session.state
  return { kind, title: titles[session.state] ?? session.state }
}

/**
 * Status dot. `working` animates like the terminal's spinner; the rest are
 * static colours: green means the round finished and wants review, yellow
 * means queued input, red means a terminal holds the session.
 *
 * Decorative: the status is repeated in the row's own tooltip, so labelling
 * the dot would only pad every tab's accessible name.
 */
function SessionState({ session }: { session: SessionSummary }) {
  const { kind } = sessionStatus(session)
  if (kind === 'working') {
    // `busy`, not `working`: the timeline's global .working rule would
    // override this element's fixed width and break name alignment.
    return (
      <span className="session-state busy" aria-hidden="true">
        <i /><i /><i />
      </span>
    )
  }
  return <span className={`session-state ${kind}`} aria-hidden="true" />
}

/**
 * Fixed left rail listing every live session, one per row. The list scrolls
 * on its own so a long list never pushes the conversation around, and on
 * narrow screens the rail slides in as a drawer over the timeline.
 */
export function SessionSidebar({
  sessions,
  activeId,
  busyId,
  open,
  onSelect,
  onAttach,
  onDetach,
  onClose,
}: {
  sessions: SessionSummary[]
  activeId: string | null
  busyId: string | null
  open: boolean
  onSelect: (id: string) => void
  onAttach: (id: string) => void
  onDetach: (id: string) => void
  onClose: () => void
}) {
  const attached = sessions.filter((session) => session.attached_by === 'web')
  const others = sessions.filter((session) => session.attached_by !== 'web')
  return (
    <>
      {open ? <div className="sidebar-scrim" onClick={onClose} aria-hidden="true" /> : null}
      <nav className={open ? 'sidebar open' : 'sidebar'} aria-label="Agent sessions">
        <div className="session-scroll">
          {attached.length === 0 && others.length === 0 ? (
            <p className="sidebar-empty">No live sessions yet.</p>
          ) : null}
          {attached.length > 0 ? (
            <ul className="session-list">
              {attached.map((session) => (
                <li key={session.id} className="session-row" data-session={session.id}>
                  <button
                    type="button"
                    className={session.id === activeId ? 'session-tab active' : 'session-tab'}
                    onClick={() => onSelect(session.id)}
                    title={`${sessionStatus(session).title} · ${session.cwd}`}
                  >
                    <SessionState session={session} />
                    <span className="session-text">
                      <span className="session-name">{sessionLabel(session)}</span>
                      {/* Decorative: the full path is in the row's tooltip. */}
                      <span className="session-meta" aria-hidden="true">{sessionDir(session)}</span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="session-close"
                    onClick={() => onDetach(session.id)}
                    disabled={busyId === session.id}
                    aria-label={`Detach ${session.name || sessionDir(session)}`}
                    title="Detach (the session keeps running)"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {others.length > 0 ? (
            <>
              <p className="sidebar-heading">Detached</p>
              <ul className="session-list">
                {others.map((session) => {
                  const owned = session.attached_by === 'terminal'
                  return (
                    <li key={session.id} className="session-row" data-session={session.id}>
                      <button
                        type="button"
                        className="session-tab detached"
                        onClick={() => onAttach(session.id)}
                        disabled={owned || busyId === session.id}
                        title={
                          owned
                            ? `${sessionStatus(session).title} · ${session.cwd}`
                            : `Attach · ${session.cwd}`
                        }
                      >
                        <SessionState session={session} />
                        <span className="session-text">
                          <span className="session-name">{sessionLabel(session)}</span>
                          <span className="session-meta" aria-hidden="true">{sessionDir(session)}</span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </>
          ) : null}
        </div>
      </nav>
    </>
  )
}

export function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const goingLight = theme === 'dark'
  return (
    <button
      className="theme-toggle"
      type="button"
      onClick={onToggle}
      aria-label={goingLight ? 'Switch to light theme' : 'Switch to dark theme'}
      title={goingLight ? 'Light theme' : 'Dark theme'}
    >
      {goingLight ? '☀' : '☾'}
    </button>
  )
}

// Scroll-to-top affordance in the fixed top controls. Hidden until the user
// has scrolled down a meaningful amount so it never clutters the initial view.
export function NewSessionButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="session-new"
      type="button"
      onClick={onClick}
      aria-label="New session"
      title="New session"
    >
      +
    </button>
  )
}

/** Opens the session rail when it is collapsed into a drawer (narrow screens). */
export function SidebarToggle({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="sidebar-toggle"
      type="button"
      onClick={onClick}
      aria-label="Show sessions"
      title="Sessions"
    >
      ☰
    </button>
  )
}

export function ScrollTopButton({ onClick }: { onClick: () => void }) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 400)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  if (!visible) return null
  return (
    <button
      className="scroll-top"
      type="button"
      onClick={onClick}
      aria-label="Scroll to top"
      title="Scroll to top"
    >
      ↑
    </button>
  )
}

// Light shell-style highlight: distinguish the program name, flags, and
// quoted strings. Purely presentational — React escapes every text node.
function HighlightedCommand({ text }: { text: string }) {
  const tokens = text.match(/\s+|"[^"]*"|'[^']*'|[^\s]+/g) ?? [text]
  let namePlaced = false
  return (
    <code className="command">
      {tokens.map((token, index) => {
        if (/^\s+$/.test(token)) return <span key={index}>{token}</span>
        let kind = 'cmd-arg'
        if (/^["']/.test(token)) kind = 'cmd-str'
        else if (/^-/.test(token)) kind = 'cmd-flag'
        else if (!namePlaced) {
          kind = 'cmd-name'
          namePlaced = true
        }
        return <span key={index} className={kind}>{token}</span>
      })}
    </code>
  )
}

function CommandPreview({ detail }: { detail: string }) {
  // Summaries arrive as "<tool>: <detail>"; peel the tool label off so the
  // command/path renders on its own with highlighting.
  const separator = detail.indexOf(': ')
  const label = separator > 0 ? detail.slice(0, separator) : ''
  const body = separator > 0 ? detail.slice(separator + 2) : detail
  return (
    <div className="prompt-command">
      {label ? <span className="cmd-label">{label}</span> : null}
      <HighlightedCommand text={body} />
    </div>
  )
}

function compactTokens(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`
  return String(value)
}

function formatDuration(value: number) {
  const total = Math.max(0, Math.ceil(value))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s`
  if (minutes) return `${minutes}m ${String(seconds).padStart(2, '0')}s`
  return `${seconds}s`
}

function formatRoundDuration(value: number) {
  const total = Math.max(0, Math.floor(value))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes) return `${minutes}m`
  return `${seconds}s`
}

export type ContextStatusProps = {
  contextTokens: number
  contextLimit: number
  inputTokens: number
  outputTokens: number
  cachedTokens: number
}

export type ThinkingProps = {
  suffix?: string
  contextTokens?: number
  contextLimit?: number
  inputTokens?: number
  outputTokens?: number
  cachedTokens?: number
  label?: string
  progress?: boolean
  countdown?: number
  startedAt?: number
  roundElapsed?: number
}

function ContextUsage({
  contextTokens,
  contextLimit,
  inputTokens,
  outputTokens,
  cachedTokens,
}: ContextStatusProps) {
  const fraction = contextLimit > 0
    ? Math.min(Math.max(contextTokens / contextLimit, 0), 1)
    : 0
  const contextLevel = fraction >= 0.9 ? 'danger' : fraction >= 0.75 ? 'warning' : 'info'
  const cacheHitRatio = inputTokens > 0
    ? Math.min(Math.max(cachedTokens / inputTokens, 0), 1)
    : 0
  const details = `↑${compactTokens(inputTokens)} · ↓${compactTokens(outputTokens)} · ${Math.round(cacheHitRatio * 100)}% hit`
  if (contextLimit <= 0) {
    return <div className="context-usage"><small>~{compactTokens(contextTokens)} · {details}</small></div>
  }
  return (
    <div className="context-usage" aria-label="context usage">
      <i className={`context-progress ${contextLevel}`} aria-hidden="true">
        <i style={{ width: `${fraction * 100}%` }} />
      </i>
      <strong className={contextLevel}>{Math.round(fraction * 100)}%</strong>
      <small>{details}</small>
    </div>
  )
}

export function Thinking({
  suffix = '',
  contextTokens = 0,
  contextLimit = 0,
  inputTokens = 0,
  outputTokens = 0,
  cachedTokens = 0,
  label = 'Working',
  progress = false,
  countdown,
  startedAt,
  roundElapsed,
}: ThinkingProps) {
  const mountedAt = useRef(Date.now())
  const start = startedAt ?? mountedAt.current
  const elapsed = () => Math.max(0, Math.floor((Date.now() - start) / 1000))
  const [seconds, setSeconds] = useState(elapsed)
  useEffect(() => {
    setSeconds(elapsed())
    const id = window.setInterval(() => setSeconds(elapsed()), 1000)
    return () => window.clearInterval(id)
  }, [start])
  return (
    <div className="activity-operation" aria-label="working">
      <div className="activity-operation-line">
        <span className="activity-pulse" aria-hidden="true"><i /><i /><i /></span>
        <strong>{label}</strong>
        {suffix ? <small className="activity-suffix">{suffix}</small> : null}
        {progress ? <i className="indeterminate-progress" aria-hidden="true"><i /></i> : null}
        {contextLimit > 0 || contextTokens > 0 ? (
          <ContextUsage
            contextTokens={contextTokens}
            contextLimit={contextLimit}
            inputTokens={inputTokens}
            outputTokens={outputTokens}
            cachedTokens={cachedTokens}
          />
        ) : null}
        <time>{countdown == null ? `${seconds}s` : formatDuration(countdown - seconds)}</time>
        {roundElapsed == null ? null : <small className="round-elapsed">· {formatRoundDuration(roundElapsed)}</small>}
      </div>
    </div>
  )
}

export function ActivityStatus({
  busy,
  status,
  processStatus = { running: 0, finished: 0, processes: [] },
  contextStatus = null,
}: {
  busy: boolean
  status: ThinkingProps | null
  processStatus?: ProcessStatus
  contextStatus?: ContextStatusProps | null
}) {
  const active = busy || status !== null
  const hasProcesses = processStatus.running > 0 || processStatus.processes.length > 0
  if (!active && !hasProcesses && contextStatus === null) return null
  return (
    <aside className="activity-dock" aria-label="Session activity">
      {active ? <Thinking {...(status ?? {})} {...(contextStatus ?? {})} /> : (
        contextStatus ? <ContextUsage {...contextStatus} /> : null
      )}
      {hasProcesses ? (
        <section className="process-panel" aria-label="background processes">
          <ul>
            {processStatus.processes.map((process) => (
              <li key={process.process_id}>
                <span className="process-indicator" aria-hidden="true" />
                <span className="process-copy">
                  <strong><code>#{process.process_id}</code> {process.label}</strong>
                  {process.last_line ? <small>{process.last_line}</small> : <small>Waiting for output…</small>}
                </span>
                <time>{formatDuration(process.elapsed_seconds)}</time>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </aside>
  )
}

export function Login({ onSuccess }: { onSuccess: () => void }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!token || busy) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/login', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ token }),
      })
      if (!response.ok) {
        setError(response.status === 429 ? 'Too many attempts. Try again shortly.' : 'That token is not valid.')
        return
      }
      setToken('')
      onSuccess()
    } catch {
      setError('Could not reach ene.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-shell">
      <form className="login" onSubmit={submit}>
        <div className="login-row">
          <input
            type="password"
            autoComplete="current-password"
            enterKeyHint="go"
            aria-label="Access token"
            placeholder="Access token"
            value={token}
            disabled={busy}
            onChange={(event) => setToken(event.target.value)}
            required
          />
          <button
            className="login-submit"
            type="submit"
            disabled={busy || !token}
            aria-label={busy ? 'Submitting' : 'Submit'}
            title="Submit"
          >
            {busy ? '…' : '↑'}
          </button>
        </div>
      </form>
      <p className="form-error" role="alert">{error}</p>
    </main>
  )
}

export function PromptDialog({
  prompt,
  connected = true,
  onAnswer,
}: {
  prompt: Prompt
  connected?: boolean
  onAnswer: (answer: string) => void
}) {
  const [answer, setAnswer] = useState(prompt.default)
  useEffect(() => setAnswer(prompt.default), [prompt.id, prompt.default])

  // Highlight the default choice; fall back to the first when the default is
  // absent from the list so exactly one button always reads as primary.
  const primary = prompt.choices.includes(prompt.default) ? prompt.default : prompt.choices[0]

  return (
    <div className="prompt-backdrop">
      <section className="prompt" role="dialog" aria-modal="true" aria-labelledby="prompt-message">
        <p className="prompt-kicker">{prompt.kind === 'select' ? 'Action required' : 'Your input'}</p>
        {(() => {
          const [head, ...rest] = prompt.message.split('\n')
          const detail = rest.join('\n').trim()
          return (
            <>
              <div id="prompt-message" className="prompt-message">{head}</div>
              {detail ? <CommandPreview detail={detail} /> : null}
            </>
          )
        })()}
        <div className="prompt-options">
          {prompt.kind === 'select' ? prompt.choices.map((choice) => (
            <button
              type="button"
              key={choice}
              className={choice === primary ? 'primary' : undefined}
              disabled={!connected}
              onClick={() => onAnswer(choice)}
            >
              {choice}
            </button>
          )) : (
            <>
              <textarea autoFocus rows={3} value={answer} onChange={(event) => setAnswer(event.target.value)} />
              <button type="button" disabled={!connected} onClick={() => onAnswer(answer)}>Submit response</button>
            </>
          )}
        </div>
      </section>
    </div>
  )
}

export function Composer({
  activity,
  operationId,
  pending,
  busy,
  connected = true,
  draft,
  commands = {},
  onDraftChange,
  onSend,
  onWithdraw,
  onCancel,
}: {
  activity?: ReactNode
  operationId: string | null
  pending: PendingMessage | null
  busy: boolean
  connected?: boolean
  draft: string
  commands?: Record<string, string>
  onDraftChange: (text: string) => void
  onSend: (text: string) => void
  onWithdraw: () => void
  onCancel: () => void
}) {
  const text = draft
  const setText = onDraftChange
  const field = useRef<HTMLTextAreaElement>(null)
  const shell = useRef<HTMLElement>(null)
  const [completionIndex, setCompletionIndex] = useState(0)
  const [dismissedCompletion, setDismissedCompletion] = useState('')
  const commandToken = /^\/[\w-]*$/.test(text) ? text.slice(1).toLowerCase() : null
  const completions = commandToken === null || dismissedCompletion === text
    ? []
    : Object.entries(commands).filter(([name]) => name.toLowerCase().startsWith(commandToken))
  const visibleCompletions = completions.length === 1 && completions[0][0].toLowerCase() === commandToken
    ? []
    : completions

  useEffect(() => {
    setCompletionIndex(0)
    if (dismissedCompletion && dismissedCompletion !== text) setDismissedCompletion('')
  }, [dismissedCompletion, text])

  // Grow the single-line field to fit wrapped/multi-line input, up to the CSS
  // max-height (then it scrolls). Runs on every value change.
  useEffect(() => {
    const el = field.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [text])

  // Keep the timeline's bottom reserve in sync with the composer's real height,
  // so a multi-line composer never hides the tail of the conversation and the
  // last messages can always be scrolled clear of it.
  useEffect(() => {
    const el = shell.current
    if (!el) return
    const root = document.documentElement
    const update = () => {
      // Add a small gap so the last message doesn't sit flush against the bar.
      root.style.setProperty('--composer-reserve', `${el.offsetHeight + 24}px`)
    }
    update()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => {
      observer.disconnect()
      root.style.removeProperty('--composer-reserve')
    }
  }, [])

  function submit() {
    const value = text.trim()
    if (!value || busy || pending) return
    onSend(value)
    field.current?.focus()
  }

  function complete(index: number) {
    const command = visibleCompletions[index]
    if (!command) return
    setText(`/${command[0]}`)
    field.current?.focus()
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (visibleCompletions.length) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setCompletionIndex((current) => (current + 1) % visibleCompletions.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setCompletionIndex((current) => (current - 1 + visibleCompletions.length) % visibleCompletions.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        complete(completionIndex)
        return
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setDismissedCompletion(text)
        return
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <section className="composer-shell" ref={shell}>
      {activity}
      {pending ? (
        <button
          className="pending-message"
          type="button"
          onClick={onWithdraw}
          disabled={!connected || busy || Boolean(text)}
          title={text ? 'Clear the draft before editing the pending message' : 'Withdraw and edit pending message'}
        >
          <span>Pending</span>
          <strong>{pending.text}</strong>
          <em>Edit</em>
        </button>
      ) : null}
      <div className="composer">
        {visibleCompletions.length ? (
          <div className="command-completions" id="command-completions" role="listbox">
            {visibleCompletions.map(([name, description], index) => (
              <button
                className={index === completionIndex ? 'selected' : undefined}
                id={`command-completion-${index}`}
                key={name}
                type="button"
                role="option"
                aria-selected={index === completionIndex}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => complete(index)}
                onMouseEnter={() => setCompletionIndex(index)}
              >
                <strong>/{name}</strong>
                <span>{description}</span>
              </button>
            ))}
          </div>
        ) : null}
        <textarea
          ref={field}
          rows={1}
          maxLength={32768}
          placeholder={operationId ? 'Queue a message...' : 'Type Anything...'}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={keyDown}
          aria-autocomplete="list"
          aria-controls={visibleCompletions.length ? 'command-completions' : undefined}
          aria-expanded={visibleCompletions.length > 0}
          aria-activedescendant={visibleCompletions.length ? `command-completion-${completionIndex}` : undefined}
        />
        {operationId ? (
          <button
            className="stop-button"
            type="button"
            onClick={onCancel}
            disabled={!connected}
            aria-label="Stop"
            title="Stop"
          >
            <span />
          </button>
        ) : null}
        <button
          className="send-button"
          type="button"
          onClick={submit}
          disabled={!connected || busy || Boolean(pending)}
          aria-label="Send"
          title={connected ? 'Send' : 'Waiting for connection'}
        >
          ↑
        </button>
      </div>
    </section>
  )
}
