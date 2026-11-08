import { Link2, LogOut, Moon, Monitor, Plus, Power, Search, Sun, SunMoon, Terminal, X } from 'lucide-react'
import type { RefObject } from 'react'
import { useState } from 'react'

import { basename } from '../lib/format'
import type { SessionSummary } from '../lib/protocol'
import type { LinkStatus } from '../lib/socket'
import type { ThemeChoice } from '../lib/theme'
import { IconButton, LinkDot, Spinner } from './ui'

const STATE_LABEL: Record<string, string> = {
  working: 'Working',
  waiting: 'Will continue on its own',
  done: 'Idle',
  starting: 'Starting',
}

function matches(session: SessionSummary, needle: string) {
  if (!needle) return true
  return [session.name, session.title, session.preview, session.cwd, session.model]
    .some((value) => value?.toLowerCase().includes(needle))
}

function SessionRow({
  session,
  active,
  unread,
  busy,
  onSelect,
  onAttach,
  onEnd,
}: {
  session: SessionSummary
  active: boolean
  unread: boolean
  busy: boolean
  onSelect: () => void
  onAttach: () => void
  onEnd: () => void
}) {
  const [armed, setArmed] = useState(false)
  const owned = session.attached_by === 'web'
  const terminal = session.attached_by === 'terminal'
  const title = session.name || basename(session.cwd) || session.title
  const tasks = (session.watchers ?? 0) + (session.background ?? 0)
  return (
    <div className={`session-row ${active ? 'active' : ''} ${owned ? 'owned' : ''} ${terminal ? 'locked' : ''}`}>
      <button
        type="button"
        className="session-main"
        disabled={busy || terminal}
        onClick={owned ? onSelect : onAttach}
        data-tip={terminal ? 'Attached in a terminal — detach there first' : owned ? undefined : 'Attach'}
      >
        <span className={`state-dot ${session.state}`} aria-label={STATE_LABEL[session.state] ?? session.state} />
        <span className="session-text">
          <span className="session-title">
            <span className="clamp">{title}</span>
            {unread ? <span className="unread" aria-label="Finished" /> : null}
          </span>
          <span className="session-sub clamp">{session.preview || session.cwd}</span>
        </span>
        <span className="session-meta">
          {busy ? <Spinner size={13} /> : terminal ? <Terminal size={13} /> : !owned ? <Link2 size={13} className="attach-hint" /> : null}
          {tasks ? <span className="pill-mini" data-tip="Background processes">{tasks}</span> : null}
        </span>
      </button>
      {!terminal ? (
        <IconButton
          icon={Power}
          size="sm"
          tone="danger"
          className={`row-action ${armed ? 'armed' : ''}`}
          label={armed ? 'Click again to end session' : 'End session'}
          active={armed}
          disabled={busy}
          onClick={() => {
            if (armed) {
              setArmed(false)
              onEnd()
            } else {
              setArmed(true)
              window.setTimeout(() => setArmed(false), 3000)
            }
          }}
        />
      ) : null}
    </div>
  )
}

export function Sidebar({
  sessions,
  activeId,
  unread,
  busyId,
  open,
  status,
  theme,
  searchRef,
  onClose,
  onSelect,
  onAttach,
  onEnd,
  onNew,
  onTheme,
  onLogout,
}: {
  sessions: SessionSummary[]
  activeId: string | null
  unread: Set<string>
  busyId: string | null
  open: boolean
  status: LinkStatus
  theme: ThemeChoice
  searchRef: RefObject<HTMLInputElement | null>
  onClose: () => void
  onSelect: (id: string) => void
  onAttach: (id: string) => void
  onEnd: (id: string) => void
  onNew: () => void
  onTheme: () => void
  onLogout: () => void
}) {
  const [filter, setFilter] = useState('')
  const needle = filter.trim().toLowerCase()
  const visible = sessions.filter((session) => matches(session, needle))
  const groups: Array<[string, SessionSummary[]]> = [
    ['Open here', visible.filter((session) => session.attached_by === 'web')],
    ['Available', visible.filter((session) => !session.attached_by)],
    ['In a terminal', visible.filter((session) => session.attached_by && session.attached_by !== 'web')],
  ]
  const ThemeIcon = theme === 'light' ? Sun : theme === 'dark' ? Moon : SunMoon
  return (
    <>
      <div className={`sidebar-scrim ${open ? 'open' : ''}`} onClick={onClose} />
      <nav className={`sidebar glass ${open ? 'open' : ''}`} aria-label="Sessions">
        <div className="sidebar-head">
          <img src="/favicon.svg" alt="" className="logo" />
          <span className="brand">ene</span>
          <span className="grow" />
          <IconButton icon={Plus} label="New session" tone="accent" onClick={onNew} />
          <IconButton icon={X} label="Close" className="show-narrow" onClick={onClose} />
        </div>
        <label className="filter">
          <Search size={14} />
          <input
            ref={searchRef}
            value={filter}
            placeholder="Search sessions  ⌘K"
            onChange={(event) => setFilter(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && visible[0]) {
                const first = visible[0]
                if (first.attached_by === 'web') onSelect(first.id)
                else if (!first.attached_by) onAttach(first.id)
              } else if (event.key === 'Escape') {
                setFilter('')
                event.currentTarget.blur()
              }
            }}
          />
        </label>
        <div className="session-groups">
          {sessions.length === 0 ? (
            <div className="sidebar-empty">
              <Monitor size={22} strokeWidth={1.4} />
              <span>No live sessions</span>
              <button type="button" className="btn primary" onClick={onNew}><Plus size={15} />New session</button>
            </div>
          ) : null}
          {groups.map(([label, items]) => items.length ? (
            <section key={label}>
              <h3>{label}<span>{items.length}</span></h3>
              {items.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  active={session.id === activeId}
                  unread={unread.has(session.id)}
                  busy={busyId === session.id}
                  onSelect={() => onSelect(session.id)}
                  onAttach={() => onAttach(session.id)}
                  onEnd={() => onEnd(session.id)}
                />
              ))}
            </section>
          ) : null)}
        </div>
        <div className="sidebar-foot">
          <LinkDot status={status} />
          <span className="muted small">{status === 'open' ? 'Hub connected' : 'Hub link down'}</span>
          <span className="grow" />
          <IconButton icon={ThemeIcon} size="sm" label={`Theme: ${theme}`} onClick={onTheme} />
          <IconButton icon={LogOut} size="sm" label="Sign out" onClick={onLogout} />
        </div>
      </nav>
    </>
  )
}
