import { PanelLeft } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Login } from './components/Login'
import { NewSessionDialog } from './components/NewSession'
import { ResizeHandle } from './components/ResizeHandle'
import type { InspectorTab } from './components/Inspector'
import { isInspectorTab, rememberInspectorTab } from './components/Inspector'
import { SessionPane } from './components/SessionPane'
import { Sidebar } from './components/Sidebar'
import { IconButton, LinkBanner, Spinner, Toasts, useMedia } from './components/ui'
import { armChime, loadSound, playChime, saveSound } from './lib/chime'
import { attachSession, detachSession, endSession, errorMessage, logout, setCsrf } from './lib/api'
import { sessionTitle } from './lib/format'
import type { SessionSummary } from './lib/protocol'
import { requestFilesFocus } from './lib/search'
import { useSocket } from './lib/socket'
import { store } from './lib/storage'
import type { ThemeChoice } from './lib/theme'
import { applyTheme, loadTheme, nextTheme } from './lib/theme'
import { inTerminal } from './lib/terminalSocket'
import { toast } from './lib/toast'

const narrow = () => window.matchMedia?.('(max-width: 1100px)').matches ?? false

function loadInspector(): InspectorTab | null {
  if (narrow()) return null
  const value = store.get('ene.inspector')
  return isInspectorTab(value) ? value : null
}

export default function App() {
  // null: unknown (connecting), false: needs login, true: signed in.
  const [auth, setAuth] = useState<boolean | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  // The hub's machine name, used to tell several hubs apart; falls back to
  // the address in the URL until the hub reports it.
  const [host, setHost] = useState(() => location.hostname || 'ene')
  const [activeId, setActiveId] = useState<string | null>(() => store.get('ene.active'))
  const [busyId, setBusyId] = useState<string | null>(null)
  const [unread, setUnread] = useState<Set<string>>(new Set())
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(() => store.get('ene.sidebar') === 'rail')
  const isNarrow = useMedia('(max-width: 820px)')
  const rail = collapsed && !isNarrow
  const narrowRef = useRef(isNarrow)
  narrowRef.current = isNarrow
  const toggleRail = useCallback(() => {
    setCollapsed((value) => {
      store.set('ene.sidebar', value ? null : 'rail')
      return !value
    })
  }, [])
  const [inspector, setInspectorState] = useState<InspectorTab | null>(loadInspector)
  const [findOpen, setFindOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [theme, setTheme] = useState<ThemeChoice>(loadTheme)
  const [sound, setSound] = useState(loadSound)
  const soundRef = useRef(sound)
  soundRef.current = sound
  const searchRef = useRef<HTMLInputElement>(null)
  const previousStates = useRef(new Map<string, string>())
  const listSignature = useRef('')
  // Local edits diverge from the last list the hub sent; forget it so the
  // hub's next list is always applied.
  const updateSessions = useCallback((update: SessionSummary[] | ((current: SessionSummary[]) => SessionSummary[])) => {
    listSignature.current = ''
    setSessions(update)
  }, [])
  const activeRef = useRef(activeId)
  activeRef.current = activeId

  useEffect(() => applyTheme(theme), [theme])
  useEffect(() => armChime(), [])

  const setInspector = useCallback((tab: InspectorTab | null) => {
    setInspectorState(tab)
    if (tab) rememberInspectorTab(tab)
    if (!narrow()) store.set('ene.inspector', tab)
  }, [])

  const select = useCallback((id: string | null) => {
    setActiveId(id)
    store.set('ene.active', id)
    setSidebarOpen(false)
    if (id) {
      setUnread((current) => {
        if (!current.has(id)) return current
        const next = new Set(current)
        next.delete(id)
        return next
      })
    }
  }, [])

  const control = useSocket({
    enabled: auth !== false,
    url: () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`,
    fatalCodes: [4403],
    onFatal: () => setAuth(false),
    onMessage: (message) => {
      if (message.type === 'focus') {
        // A session forked from the one in view opens in its place.
        if (typeof message.id === 'string' && message.source === activeRef.current) select(message.id)
        return
      }
      if (message.type !== 'sessions') return
      if (typeof message.csrf === 'string') setCsrf(message.csrf)
      if (typeof message.host === 'string' && message.host) setHost(message.host)
      setAuth(true)
      const list = Array.isArray(message.sessions) ? (message.sessions as SessionSummary[]) : []
      // A session that finishes while not in view gets an unread marker.
      const finished: string[] = []
      for (const session of list) {
        const before = previousStates.current.get(session.id)
        if (before === 'working' && session.state !== 'working' &&
          (session.id !== activeRef.current || document.visibilityState === 'hidden')) {
          finished.push(session.id)
        }
      }
      previousStates.current = new Map(list.map((session) => [session.id, session.state]))
      // One chime per update, however many sessions finished together.
      if (finished.length && soundRef.current) playChime()
      setUnread((current) => {
        const alive = new Set(list.map((session) => session.id))
        const next = new Set([...current, ...finished].filter((id) => alive.has(id)))
        return next.size === current.size && [...next].every((id) => current.has(id)) ? current : next
      })
      // The hub re-sends the list on every change to any session; an
      // identical list must not re-render every pane.
      const signature = JSON.stringify(list)
      if (signature !== listSignature.current) {
        listSignature.current = signature
        setSessions(list)
      }
    },
  })

  const attached = sessions.filter((session) => session.attached_by === 'web')

  // Keep the active pane valid as sessions come and go.
  useEffect(() => {
    if (auth !== true) return
    if (activeId && attached.some((session) => session.id === activeId)) return
    const fallback = attached[0]?.id ?? null
    if (fallback !== activeId) select(fallback)
  }, [attached, activeId, auth, select])

  const run = useCallback(async (id: string, action: () => Promise<void>) => {
    if (busyId) return
    setBusyId(id)
    try {
      await action()
    } catch (exc) {
      toast(errorMessage(exc))
    } finally {
      setBusyId(null)
    }
  }, [busyId])

  const markOwned = useCallback((session: SessionSummary) => {
    updateSessions((current) => current.some((item) => item.id === session.id)
      ? current.map((item) => (item.id === session.id ? { ...item, ...session } : item))
      : [...current, session])
  }, [updateSessions])

  const attach = useCallback((id: string) => run(id, async () => {
    const session = await attachSession(id)
    markOwned(session)
    select(id)
  }), [markOwned, run, select])

  const detach = useCallback((id: string) => run(id, async () => {
    await detachSession(id)
    updateSessions((current) => current.map((item) => (item.id === id ? { ...item, attached_by: '' } : item)))
    store.set(`ene.draft.${id}`, null)
  }), [run, updateSessions])

  const end = useCallback((id: string) => run(id, async () => {
    await endSession(id)
    updateSessions((current) => current.filter((item) => item.id !== id))
    store.set(`ene.draft.${id}`, null)
    toast('Session ended.', 'success', 2500)
  }), [run, updateSessions])

  const signOut = useCallback(async () => {
    try {
      await logout()
    } catch {
      // Local sign-out proceeds regardless.
    }
    updateSessions([])
    setAuth(false)
  }, [updateSessions])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // A focused terminal gets every key (Ctrl+B, Ctrl+F, … mean something there).
      if (inTerminal(event.target)) return
      const mod = event.metaKey || event.ctrlKey
      const key = event.key.toLowerCase()
      if (mod && key === 'k' && !event.shiftKey) {
        event.preventDefault()
        setSidebarOpen(true)
        if (!narrowRef.current) {
          setCollapsed(false)
          store.set('ene.sidebar', null)
        }
        window.setTimeout(() => {
          searchRef.current?.focus()
          searchRef.current?.select()
        }, 30)
      } else if (mod && !event.altKey && activeRef.current && ((key === 'p' && !event.shiftKey) || (key === 'f' && event.shiftKey))) {
        // Cmd/Ctrl+P finds files; Cmd/Ctrl+Shift+F searches their contents.
        event.preventDefault()
        setInspector('files')
        requestFilesFocus(key === 'p' ? 'find' : 'search')
      } else if (mod && key === 'f' && !event.shiftKey && activeRef.current) {
        event.preventDefault()
        setFindOpen(true)
      } else if (mod && key === 'b' && !event.shiftKey) {
        event.preventDefault()
        if (narrowRef.current) setSidebarOpen((open) => !open)
        else toggleRail()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggleRail, setInspector])

  const reviewCount = unread.size
  const activeSession = attached.find((session) => session.id === activeId)
  useEffect(() => {
    const name = activeSession ? sessionTitle(activeSession) : ''
    document.title = `${reviewCount ? `(${reviewCount}) ` : ''}${name ? `${name} · ` : ''}${host}`
  }, [reviewCount, activeSession, host])

  if (auth === false) return <><Login onSuccess={() => setAuth(null)} /><Toasts /></>
  if (auth === null) {
    return (
      <main className="boot">
        <Spinner size={22} />
        <LinkBanner status={control.status} onRetry={() => control.retry()} />
      </main>
    )
  }

  return (
    <div className={`app ${inspector ? 'with-inspector' : ''} ${rail ? 'rail-mode' : ''}`}>
      <Sidebar
        sessions={sessions}
        host={host}
        activeId={activeId}
        unread={unread}
        busyId={busyId}
        open={sidebarOpen}
        status={control.status}
        theme={theme}
        searchRef={searchRef}
        rail={rail}
        onToggleRail={toggleRail}
        onClose={() => setSidebarOpen(false)}
        onSelect={select}
        onAttach={attach}
        onDetach={detach}
        onEnd={end}
        onNew={() => {
          setCreating(true)
          setSidebarOpen(false)
        }}
        onTheme={() => setTheme(nextTheme(theme))}
        sound={sound}
        onSound={() => {
          saveSound(!sound)
          setSound(!sound)
          // Turning it on doubles as a preview (and a user gesture to unlock audio).
          if (!sound) playChime()
        }}
        onLogout={signOut}
      />
      <main className="workspace">
        {rail ? null : <ResizeHandle panel="sidebar" />}
        {attached.map((session) => (
          <SessionPane
            key={session.id}
            summary={session}
            active={session.id === activeId}
            inspector={inspector}
            onInspector={setInspector}
            onDetach={() => detach(session.id)}
            detaching={busyId === session.id}
            onGone={() => updateSessions((current) => current.map((item) => (item.id === session.id ? { ...item, attached_by: '' } : item)))}
            onMenu={() => setSidebarOpen(true)}
            findOpen={findOpen}
            onFindOpen={setFindOpen}
          />
        ))}
        {attached.length === 0 ? (
          <div className="welcome">
            <IconButton icon={PanelLeft} label="Sessions" className="menu-btn floating" onClick={() => setSidebarOpen(true)} />
            <img src="/favicon.svg" alt="" className="welcome-logo" />
            <p className="welcome-hint">Start or attach a session from the sidebar.</p>
          </div>
        ) : null}
      </main>
      {attached.length === 0 ? (
        <div className="global-banner"><LinkBanner status={control.status} onRetry={() => control.retry()} /></div>
      ) : null}
      {creating ? (
        <NewSessionDialog
          onClose={() => setCreating(false)}
          onCreated={(session) => {
            markOwned(session)
            setCreating(false)
            select(session.id)
          }}
        />
      ) : null}
      <Toasts />
    </div>
  )
}
