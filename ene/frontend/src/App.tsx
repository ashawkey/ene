import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { ApiError, attachSession, createSession, detachSession } from './api'
import { ActivityStatus, Composer, ConnectionBanner, Login, NewSessionButton, PromptDialog, ScrollTopButton, SessionSidebar, SidebarToggle, ThemeToggle } from './components'
import type { ContextStatusProps, ThinkingProps } from './components'
import { useConnectionSocket } from './connection'
import { NewSessionDialog } from './NewSessionDialog'
import { EventCard } from './renderers'
import { applyTheme, hasStoredTheme, resolveInitialTheme, storeTheme } from './theme'
import type { Theme } from './theme'
import type { AgentEvent, ClientAction, DisplayEvent, NewSessionRequest, PendingMessage, Prompt, SessionSummary, StateMessage } from './types'
import { displayTypes, isPrompt } from './types'
import { appendDelta, finalizeStream } from './streaming'

function parseContextStatus(data: Record<string, unknown>): ContextStatusProps | null {
  if (typeof data.context_tokens !== 'number') return null
  return {
    contextTokens: data.context_tokens,
    contextLimit: typeof data.context_limit === 'number' ? data.context_limit : 0,
    inputTokens: typeof data.input_tokens === 'number' ? data.input_tokens : 0,
    outputTokens: typeof data.output_tokens === 'number' ? data.output_tokens : 0,
    cachedTokens: typeof data.cached_tokens === 'number' ? data.cached_tokens : 0,
  }
}

function appendEvent(events: DisplayEvent[], event: DisplayEvent) {
  const previous = events.at(-1)
  if (previous && previous.type === event.type && ['output', 'system'].includes(event.type)) {
    const separator = previous.text && event.text ? '\n' : ''
    return [
      ...events.slice(0, -1),
      { ...previous, text: `${previous.text}${separator}${event.text}`, data: { ...previous.data, ...event.data } },
    ]
  }
  return [...events, event]
}

/**
 * One agent session. Every session gets a pane that stays mounted with its own
 * always-open websocket, so switching tabs only toggles visibility — no
 * reconnect and no history replay. Composer/prompt render only for the active
 * pane, so exactly one of each exists in the DOM.
 */
function SessionPane({
  sessionId,
  active,
  draft,
  restoreScroll,
  showConnectionStatus,
  onDraftChange,
}: {
  sessionId: string
  active: boolean
  draft: string
  restoreScroll: number | undefined
  showConnectionStatus: boolean
  onDraftChange: (text: string) => void
}) {
  const [events, setEvents] = useState<DisplayEvent[]>([])
  const [operationId, setOperationId] = useState<string | null>(null)
  const [prompt, setPrompt] = useState<Prompt | null>(null)
  const [pending, setPending] = useState<PendingMessage | null>(null)
  const [submitAction, setSubmitAction] = useState<string | null>(null)
  const [withdrawAction, setWithdrawAction] = useState<string | null>(null)
  const submitActionRef = useRef<string | null>(null)
  const withdrawActionRef = useRef<string | null>(null)
  const [thinkingStatus, setThinkingStatus] = useState<ThinkingProps | null>(null)
  const [contextStatus, setContextStatus] = useState<ContextStatusProps | null>(null)
  const [processStatus, setProcessStatus] = useState('')
  const [commands, setCommands] = useState<Record<string, string>>({})
  const lastSeq = useRef(0)
  const streamKey = useRef('')
  const localKey = useRef(0)
  const pinned = useRef(true)
  const activeRef = useRef(active)
  activeRef.current = active
  // Window scrolling is asynchronous. Guard every scheduled scroll with this
  // pane's current visibility so a callback from a pane that was just hidden
  // cannot move the newly selected session.
  const scrollToTail = useCallback(() => {
    requestAnimationFrame(() => {
      if (activeRef.current) {
        window.scrollTo({ top: document.documentElement.scrollHeight })
      }
    })
  }, [])

  const showEvent = useCallback((type: string, text: string, data = {}, seq?: number) => {
    const key = seq ? `event-${seq}` : `local-${++localKey.current}`
    setEvents((current) => appendEvent(current, { key, type, text, data }))
  }, [])

  const handleMessage = useCallback((message: AgentEvent) => {
    if (message.type === 'state') {
      const state = message as StateMessage
      const key = `${state.session}:${state.stream_id}`
      const streamChanged = Boolean(streamKey.current && streamKey.current !== key)
      if (streamChanged) {
        lastSeq.current = 0
        setEvents([])
      }
      streamKey.current = key
      setOperationId(state.operation_id)
      setProcessStatus(state.process_status)
      setCommands(state.commands ?? {})
      setContextStatus(state.context_status ? parseContextStatus(state.context_status) : null)
      // State frames are authoritative after reconnect.
      if (state.operation_id === null || state.active_indicator === null) {
        setThinkingStatus(null)
      } else {
        const active = state.active_indicator
        const context = parseContextStatus(active)
        setThinkingStatus({
          suffix: context === null && typeof active.suffix === 'string' ? active.suffix : '',
          contextTokens: typeof active.context_tokens === 'number' ? active.context_tokens : 0,
          contextLimit: typeof active.context_limit === 'number' ? active.context_limit : 0,
          inputTokens: typeof active.input_tokens === 'number' ? active.input_tokens : 0,
          outputTokens: typeof active.output_tokens === 'number' ? active.output_tokens : 0,
          cachedTokens: typeof active.cached_tokens === 'number' ? active.cached_tokens : 0,
          label: typeof active.label === 'string' ? active.label : 'Working',
          startedAt: typeof active.started_at === 'number' ? active.started_at * 1000 : Date.now(),
          progress: active.progress === true,
          countdown: typeof active.countdown === 'number' ? active.countdown : undefined,
          roundElapsed: typeof active.round_elapsed === 'number' ? active.round_elapsed : undefined,
        })
      }
      setPrompt(state.prompt)
      setPending(state.pending)
      const submitId = submitActionRef.current
      if (submitId !== null && state.pending?.action_id === submitId) {
        onDraftChange('')
        submitActionRef.current = null
      }
      setSubmitAction(null)
      setWithdrawAction(null)
      if (state.replay_truncated) {
        showEvent(
          'warning',
          `Earlier events are no longer available; replay starts at event ${state.oldest_seq}.`,
        )
      }
      return
    }
    if (message.type === 'rejected') {
      const actionId = typeof message.action_id === 'string' ? message.action_id : ''
      if (actionId && actionId === submitActionRef.current) {
        submitActionRef.current = null
        setSubmitAction(null)
      } else if (actionId && actionId === withdrawActionRef.current) {
        withdrawActionRef.current = null
        setWithdrawAction(null)
        showEvent('error', message.error || 'Pending message is unavailable.')
      } else {
        showEvent('error', message.error || 'Message rejected')
      }
      return
    }
    if (message.type === 'cancel_ack') {
      if (!message.ok) setOperationId(null)
      return
    }
    if (message.type === 'accepted' || message.type === 'prompt_ack') return

    if (message.seq) {
      if (message.seq <= lastSeq.current) return
      lastSeq.current = message.seq
    }

    const data = message.data ?? {}
    switch (message.type) {
      case 'pending_set':
        setPending({
          id: typeof data.id === 'string' ? data.id : '',
          text: typeof data.text === 'string' ? data.text : '',
          source: typeof data.source === 'string' ? data.source : '',
          action_id: typeof data.action_id === 'string' ? data.action_id : null,
        })
        if (typeof data.action_id === 'string' && data.action_id === submitActionRef.current) {
          onDraftChange('')
          submitActionRef.current = null
          setSubmitAction(null)
        }
        break
      case 'pending_cleared':
        setPending(null)
        if (
          data.reason === 'withdrawn' &&
          typeof data.action_id === 'string' &&
          data.action_id === withdrawActionRef.current
        ) {
          onDraftChange(typeof data.text === 'string' ? data.text : '')
          withdrawActionRef.current = null
          setWithdrawAction(null)
        }
        break
      case 'draft_set':
        onDraftChange(typeof data.text === 'string' ? data.text : '')
        break
      case 'submission_rejected':
        if (typeof data.action_id === 'string' && data.action_id === submitActionRef.current) {
          submitActionRef.current = null
          setSubmitAction(null)
        }
        break
      case 'withdrawal_rejected':
        if (typeof data.action_id === 'string' && data.action_id === withdrawActionRef.current) {
          withdrawActionRef.current = null
          setWithdrawAction(null)
          showEvent('error', typeof data.error === 'string' ? data.error : 'Pending message is unavailable.')
        }
        break
      case 'prompt_open':
        if (isPrompt(data)) setPrompt(data)
        break
      case 'prompt_resolved':
        setPrompt(null)
        break
      case 'operation_start':
        setOperationId(typeof data.id === 'string' ? data.id : null)
        break
      case 'operation_end':
        setOperationId(null)
        setThinkingStatus(null)
        break
      case 'thinking_start': {
        const context = parseContextStatus(data)
        if (context) setContextStatus(context)
        setThinkingStatus({
          suffix: context === null && typeof data.suffix === 'string' ? data.suffix : '',
          contextTokens: typeof data.context_tokens === 'number' ? data.context_tokens : 0,
          contextLimit: typeof data.context_limit === 'number' ? data.context_limit : 0,
          inputTokens: typeof data.input_tokens === 'number' ? data.input_tokens : 0,
          outputTokens: typeof data.output_tokens === 'number' ? data.output_tokens : 0,
          cachedTokens: typeof data.cached_tokens === 'number' ? data.cached_tokens : 0,
          label: typeof data.label === 'string' ? data.label : 'Working',
          startedAt: typeof data.started_at === 'number' ? data.started_at * 1000 : Date.now(),
          progress: data.progress === true,
          countdown: typeof data.countdown === 'number' ? data.countdown : undefined,
          roundElapsed: typeof data.round_elapsed === 'number' ? data.round_elapsed : undefined,
        })
        break
      }
      case 'thinking_update':
        setThinkingStatus((current) => current === null ? null : {
          ...current,
          suffix: typeof data.suffix === 'string' ? data.suffix : current.suffix,
        })
        break
      case 'thinking_stop':
        setThinkingStatus(null)
        break
      case 'process_status':
        setProcessStatus(typeof data.text === 'string' ? data.text : '')
        break
      case 'commands':
        if (data.commands && typeof data.commands === 'object') {
          setCommands(Object.fromEntries(
            Object.entries(data.commands).filter(
              (entry): entry is [string, string] => typeof entry[1] === 'string',
            ),
          ))
        }
        break
      case 'context_status': {
        const context = parseContextStatus(data)
        if (context) setContextStatus(context)
        break
      }
      case 'timeline_reset':
        setThinkingStatus(null)
        setEvents([])
        break
      case 'assistant_delta':
      case 'thinking_delta':
        setEvents((current) => appendDelta(current, message.type, typeof data.text === 'string' ? data.text : ''))
        break
      case 'assistant_message':
      case 'thinking':
        setEvents((current) => finalizeStream(
          current,
          message.type,
          typeof data.text === 'string' ? data.text : '',
          message.seq ? `event-${message.seq}` : `local-${++localKey.current}`,
        ))
        break
      default:
        if (displayTypes.has(message.type)) {
          showEvent(message.type, typeof data.text === 'string' ? data.text : '', data, message.seq)
        }
    }
  }, [onDraftChange, showEvent])

  const connection = useConnectionSocket({
    getUrl: () => {
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const id = encodeURIComponent(sessionId)
      return `${protocol}://${location.host}/api/ws?session=${id}&after=${lastSeq.current}`
    },
    onMessage: (message) => handleMessage(message as AgentEvent),
    // 4404: the agent exited; the control channel will prune this pane.
    shouldReconnect: (event) => event.code !== 4403 && event.code !== 4404,
  })

  useEffect(() => {
    if (connection.status !== 'connected') {
      setSubmitAction(null)
      setWithdrawAction(null)
    }
  }, [connection.status])

  // Track whether new output should keep following the tail. App owns the
  // actual per-session offsets because it can capture before switching panes.
  useEffect(() => {
    if (!active) return
    const onScroll = () => {
      if (!activeRef.current) return
      const distance =
        document.documentElement.scrollHeight - window.innerHeight - window.scrollY
      pinned.current = distance < 220
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [active])

  // App captures the outgoing position before changing tabs, while its pane is
  // still visible and the browser has not clamped the window scroll offset.
  useLayoutEffect(() => {
    if (!active) return
    if (restoreScroll === undefined) {
      scrollToTail()
    } else {
      window.scrollTo({ top: restoreScroll })
    }
  }, [active, restoreScroll, scrollToTail])

  // Follow new output only. Including `active` here would schedule a second
  // tail scroll on tab activation and overwrite the restoration above.
  useEffect(() => {
    if (activeRef.current && pinned.current) scrollToTail()
  }, [events, thinkingStatus, operationId, scrollToTail])

  const send = useCallback((action: ClientAction): boolean => connection.send(action), [connection.send])

  return (
    <>
      <section className="workspace" style={active ? undefined : { display: 'none' }}>
        <div className="timeline" aria-live="polite">
          {events.map((event) => <EventCard event={event} key={event.key} />)}
        </div>
        {active ? (
          <ActivityStatus
            busy={operationId !== null}
            status={thinkingStatus}
            processStatus={processStatus}
            contextStatus={contextStatus}
          />
        ) : null}
      </section>
      {active && showConnectionStatus ? (
        <ConnectionBanner status={connection.status} onRetry={connection.retry} />
      ) : null}
      {active && prompt ? (
        <PromptDialog
          prompt={prompt}
          connected={connection.status === 'connected'}
          onAnswer={(answer) => send({ type: 'prompt_response', id: prompt.id, answer })}
        />
      ) : null}
      {active ? (
        <Composer
          operationId={operationId}
          pending={pending}
          busy={submitAction !== null || withdrawAction !== null}
          connected={connection.status === 'connected'}
          draft={draft}
          commands={commands}
          onDraftChange={onDraftChange}
          onSend={(text) => {
            if (pending || submitAction) return
            const actionId = crypto.randomUUID()
            if (!send({ type: 'submit', text, action_id: actionId })) return
            submitActionRef.current = actionId
            setSubmitAction(actionId)
            pinned.current = true
            scrollToTail()
          }}
          onWithdraw={() => {
            if (!pending || draft || withdrawAction) return
            const actionId = crypto.randomUUID()
            if (!send({ type: 'withdraw_pending', id: pending.id, action_id: actionId })) return
            withdrawActionRef.current = actionId
            setWithdrawAction(actionId)
          }}
          onCancel={() => send({ type: 'cancel', operation_id: operationId })}
        />
      ) : null}
    </>
  )
}

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [activeSession, setActiveSession] = useState<string | null>(null)
  const [theme, setTheme] = useState<Theme>(resolveInitialTheme)
  // Per-session composer drafts. Panes unmount their Composer when inactive, so
  // the in-progress text lives here to survive tab switches.
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [dialogOpen, setDialogOpen] = useState(false)
  // Drawer state for narrow screens; the rail is always visible when wide.
  const [railOpen, setRailOpen] = useState(false)
  const [busySession, setBusySession] = useState<string | null>(null)
  const [actionError, setActionError] = useState('')
  const csrf = useRef('')
  const scrollPositions = useRef(new Map<string, number>())

  // Control channel: session list + auth confirmation.
  const controlConnection = useConnectionSocket({
    enabled: authenticated !== false,
    getUrl: () => {
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      return `${protocol}://${location.host}/api/ws`
    },
    onMessage: (raw) => {
      const message = raw as AgentEvent
      if (message.type !== 'sessions') return
      if (message.csrf) csrf.current = message.csrf
      setAuthenticated(true)
      const list = message.sessions ?? []
      setSessions(list)
      // Only sessions this hub has attached get a pane to focus.
      const attached = list.filter((session) => session.attached_by === 'web')
      setActiveSession((current) => {
        if (current && attached.some((s) => s.id === current)) return current
        return attached[0]?.id ?? null
      })
    },
    shouldReconnect: (event) => event.code !== 4403,
    onTerminalClose: () => setAuthenticated(false),
  })

  useEffect(() => { applyTheme(theme) }, [theme])

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      if (!hasStoredTheme()) setTheme(query.matches ? 'dark' : 'light')
    }
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      storeTheme(next)
      return next
    })
  }, [])

  const setDraft = useCallback((sessionId: string, text: string) => {
    setDrafts((current) => ({ ...current, [sessionId]: text }))
  }, [])

  const selectSession = useCallback((sessionId: string) => {
    if (activeSession) scrollPositions.current.set(activeSession, window.scrollY)
    setActiveSession(sessionId)
    setRailOpen(false)
  }, [activeSession])

  const forgetSession = useCallback((sessionId: string) => {
    scrollPositions.current.delete(sessionId)
    setDrafts((current) => {
      const { [sessionId]: _removed, ...rest } = current
      return rest
    })
  }, [])

  // Optimistically mark ownership so the pane mounts (or unmounts) at once; the
  // control channel confirms with the authoritative list moments later.
  const markAttachment = useCallback((session: SessionSummary) => {
    setSessions((current) => {
      const known = current.some((item) => item.id === session.id)
      return known
        ? current.map((item) => (item.id === session.id ? { ...item, ...session } : item))
        : [...current, session]
    })
  }, [])

  const runSessionAction = useCallback(async (sessionId: string, action: () => Promise<void>) => {
    setBusySession(sessionId)
    setActionError('')
    try {
      await action()
    } catch (exc) {
      setActionError(exc instanceof ApiError ? exc.message : 'The action failed.')
    } finally {
      setBusySession(null)
    }
  }, [])

  const attach = useCallback((sessionId: string) => {
    void runSessionAction(sessionId, async () => {
      const session = await attachSession(csrf.current, sessionId)
      markAttachment(session)
      selectSession(sessionId)
    })
  }, [markAttachment, runSessionAction, selectSession])

  const detach = useCallback((sessionId: string) => {
    void runSessionAction(sessionId, async () => {
      await detachSession(csrf.current, sessionId)
      setSessions((current) =>
        current.map((item) => (item.id === sessionId ? { ...item, attached_by: '' } : item)),
      )
      forgetSession(sessionId)
      setActiveSession((current) => (current === sessionId ? null : current))
    })
  }, [forgetSession, runSessionAction])

  const create = useCallback(async (request: NewSessionRequest) => {
    const session = await createSession(csrf.current, request)
    markAttachment(session)
    setDialogOpen(false)
    setActionError('')
    selectSession(session.id)
  }, [markAttachment, selectSession])

  const scrollToTop = useCallback(() => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const logout = useCallback(async () => {
    try {
      await fetch('/api/logout', {
        method: 'POST',
        headers: { 'x-csrf-token': csrf.current },
      })
    } catch {
      // Ignore network failures: the local state reset below still signs out.
    }
    controlConnection.close()
    // Clearing sessions unmounts every pane, closing their sockets.
    setSessions([])
    setActiveSession(null)
    setDrafts({})
    scrollPositions.current.clear()
    setAuthenticated(false)
  }, [controlConnection.close])

  // Panes exist only for sessions this hub owns; the rest are attach targets.
  const attachedSessions = sessions.filter((session) => session.attached_by === 'web')

  if (authenticated === false) {
    return <Login onSuccess={() => setAuthenticated(null)} />
  }
  if (authenticated === null) {
    return (
      <main className="loading" aria-label="Connecting">
        <ConnectionBanner status={controlConnection.status} onRetry={controlConnection.retry} initial />
      </main>
    )
  }

  return (
    <main className="app-shell">
      <ConnectionBanner status={controlConnection.status} onRetry={controlConnection.retry} />
      <header className="top-bar">
        <SidebarToggle onClick={() => setRailOpen(true)} />
        <img className="brand-logo" src="/favicon.svg" alt="ene" />
        <NewSessionButton onClick={() => {
          setDialogOpen(true)
          setRailOpen(false)
        }} />
        <div className="top-controls">
          <ScrollTopButton onClick={scrollToTop} />
          <ThemeToggle theme={theme} onToggle={toggleTheme} />
          <button className="logout" type="button" onClick={logout} aria-label="Sign out" title="Sign out">⏻</button>
        </div>
      </header>
      <div className="layout">
        <SessionSidebar
          sessions={sessions}
          activeId={activeSession}
          busyId={busySession}
          open={railOpen}
          onSelect={selectSession}
          onAttach={attach}
          onDetach={detach}
          onClose={() => setRailOpen(false)}
        />
        <div className="panes">
          {actionError ? <p className="action-error" role="alert">{actionError}</p> : null}
          {attachedSessions.length === 0 ? (
            <section className="workspace">
              <p className="sidebar-empty">
                Create a session, or attach to a detached one from the sidebar.
              </p>
            </section>
          ) : null}
          {attachedSessions.map((session) => (
            <SessionPane
              key={session.id}
              sessionId={session.id}
              active={session.id === activeSession}
              draft={drafts[session.id] ?? ''}
              restoreScroll={scrollPositions.current.get(session.id)}
              showConnectionStatus={controlConnection.status === 'connected'}
              onDraftChange={(text) => setDraft(session.id, text)}
            />
          ))}
        </div>
      </div>
      {dialogOpen ? (
        <NewSessionDialog onCreate={create} onClose={() => setDialogOpen(false)} />
      ) : null}
    </main>
  )
}
