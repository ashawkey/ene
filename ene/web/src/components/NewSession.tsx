import { ArrowUp, Brain, Check, Clock, Cpu, Eye, EyeOff, Folder, FolderOpen, FolderPlus, History, MessageSquarePlus, Rocket, Search, Tag, UserRound, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

import { browseDirectories, conversations, createSession, errorMessage, makeDirectory, recentWorkspaces, sessionOptions } from '../lib/api'
import { basename, dateGroup, messageClock } from '../lib/format'
import type { ConversationSummary, FsListing, SessionOptions, SessionSummary } from '../lib/protocol'
import { store } from '../lib/storage'
import { IconButton, LoadingHint, Modal, Spinner } from './ui'
import { NO_AUTOFILL } from '../lib/noAutofill'

const LAST = 'ene.new.last'
// Typing settles for this long before the folder is listed and probed.
const SETTLE_MS = 250
// Rows rendered at once; a busy workspace saves thousands of conversations.
const CONVERSATION_LIMIT = 200

type Remembered = { cwd?: string; model?: string; persona?: string; reasoning_effort?: string }

function remembered(): Remembered {
  try {
    return JSON.parse(store.get(LAST) ?? '{}') as Remembered
  } catch {
    return {}
  }
}

const absolute = (path: string) => path.startsWith('/') || path.startsWith('~') || /^[A-Za-z]:[\\/]/.test(path)

/** Split a typed path into its parent folder and last component. */
function splitPath(path: string): [string, string] | null {
  const match = path.replace(/[\\/]+$/, '').match(/^(.*[\\/])([^\\/]+)$/)
  return match ? [match[1], match[2]] : null
}

/** Folder browser that follows, and sets, the working directory. */
function DirectoryPicker({ path, onNavigate }: { path: string; onNavigate: (path: string) => void }) {
  const [target, setTarget] = useState(path)
  const [listing, setListing] = useState<FsListing | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [hidden, setHidden] = useState(false)
  // The new-folder name while its row is open.
  const [naming, setNaming] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState('')
  // Bumped to list *target* again, e.g. once a missing folder is created.
  const [reload, setReload] = useState(0)

  // A typed path is listed once typing settles; clicks navigate at once (and
  // then *path* already equals *target*). Only a new path moves the listing.
  useEffect(() => {
    if (path === target) return
    const timer = window.setTimeout(() => setTarget(path), SETTLE_MS)
    return () => window.clearTimeout(timer)
  }, [path])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    browseDirectories(target)
      .then((data) => {
        if (cancelled) return
        setListing(data)
        setError('')
      })
      .catch((exc) => {
        if (cancelled) return
        setListing(null)
        setError(errorMessage(exc, 'Cannot list this folder.'))
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [target, reload])

  const go = (next: string) => {
    setTarget(next)
    setNaming(null)
    onNavigate(next)
  }

  const create = async (parent: string, name: string) => {
    if (creating || !name.trim()) return
    setCreating(true)
    setCreateError('')
    try {
      go(await makeDirectory(parent, name.trim()))
      setReload((value) => value + 1)
    } catch (exc) {
      setCreateError(errorMessage(exc, 'Could not create the folder.'))
    } finally {
      setCreating(false)
    }
  }

  const onNameKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      // Enter would otherwise submit the whole dialog.
      event.preventDefault()
      if (listing && naming !== null) void create(listing.path, naming)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setNaming(null)
      setCreateError('')
    }
  }

  // A typed folder that does not exist yet can be created in its parent.
  const missing = !listing && error && absolute(target) ? splitPath(target) : null
  const parent = listing ? listing.parent : missing?.[0] ?? ''
  const entries = listing?.entries.filter((entry) => hidden || !entry.hidden) ?? []
  return (
    <div className="dir-picker">
      <div className="panel-toolbar">
        <IconButton icon={ArrowUp} size="sm" label="Parent folder" disabled={!parent} onClick={() => go(parent)} />
        <span className="mono small clamp grow" data-tip={listing?.path}>{listing?.path ?? (target || '…')}</span>
        {loading ? <Spinner size={13} /> : null}
        <IconButton icon={hidden ? Eye : EyeOff} size="sm" label={hidden ? 'Hide dot-folders' : 'Show dot-folders'} active={hidden} onClick={() => setHidden(!hidden)} />
        <IconButton
          icon={FolderPlus}
          size="sm"
          tone="accent"
          label="New folder"
          disabled={!listing}
          active={naming !== null}
          onClick={() => {
            setNaming(naming === null ? '' : null)
            setCreateError('')
          }}
        />
      </div>
      {naming !== null ? (
        <div className="dir-new">
          <FolderPlus size={15} className="file-icon dir" />
          <input
            {...NO_AUTOFILL}
            value={naming}
            onChange={(event) => setNaming(event.target.value)}
            onKeyDown={onNameKey}
            placeholder="New folder name"
            aria-label="New folder name"
            autoFocus
            spellCheck={false}
          />
          <IconButton icon={Check} size="sm" tone="accent" label="Create folder" busy={creating} disabled={creating || !naming.trim()} onClick={() => listing && void create(listing.path, naming)} />
          <IconButton icon={X} size="sm" label="Cancel" onClick={() => { setNaming(null); setCreateError('') }} />
        </div>
      ) : null}
      {createError ? <div className="panel-error">{createError}</div> : null}
      {error ? (
        <div className="panel-error dir-missing">
          <span className="grow">{error}</span>
          {missing ? (
            <button type="button" className="btn small" disabled={creating} onClick={() => void create(missing[0], missing[1])}>
              <FolderPlus size={14} />Create “{missing[1]}”
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="dir-list">
        {entries.map((entry) => (
          <button type="button" key={entry.path} className={`file-row ${entry.hidden ? 'hidden-entry' : ''}`} onClick={() => go(entry.path)}>
            <Folder size={15} className="file-icon dir" />
            <span className="file-name clamp">{entry.name}</span>
          </button>
        ))}
        {listing && !entries.length ? <div className="dir-empty">No subfolders</div> : null}
        {!listing && !error ? <div className="center-pad"><Spinner /></div> : null}
      </div>
    </div>
  )
}

function conversationTitle(item: ConversationSummary) {
  return item.name || item.last_user_message || item.id
}

/** Saved conversations of the folder, searchable and grouped by date. */
function ConversationPicker({
  items,
  value,
  loading,
  onChange,
}: {
  items: ConversationSummary[]
  value: string
  loading: boolean
  onChange: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const needle = query.trim().toLowerCase()
  const matched = [...items]
    .sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0))
    .filter((item) => !needle || [item.name, item.last_user_message, item.id].some((text) => text?.toLowerCase().includes(needle)))
  const groups: Array<[string, ConversationSummary[]]> = []
  for (const item of matched.slice(0, CONVERSATION_LIMIT)) {
    const label = dateGroup(item.updated_at ?? 0)
    if (groups.at(-1)?.[0] === label) groups.at(-1)![1].push(item)
    else groups.push([label, [item]])
  }
  return (
    <div className="conv-picker">
      <label className="filter">
        <Search size={14} />
        <input
          {...NO_AUTOFILL}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.preventDefault()
          }}
          placeholder={items.length ? `Search ${items.length} saved conversation${items.length === 1 ? '' : 's'}` : 'Search conversations'}
          aria-label="Search conversations"
          disabled={!items.length}
        />
        {query ? <button type="button" aria-label="Clear search" onClick={() => setQuery('')}><X size={13} /></button> : null}
      </label>
      <div className="conv-list">
        <button type="button" className={`conv-row ${value === '' ? 'selected' : ''}`} aria-pressed={value === ''} onClick={() => onChange('')}>
          <MessageSquarePlus size={15} className="conv-icon" />
          <span className="conv-text"><span className="conv-title">New conversation</span></span>
        </button>
        {loading ? <LoadingHint>Loading conversations…</LoadingHint> : null}
        {!loading && !items.length ? <div className="dir-empty">No saved conversations in this folder</div> : null}
        {!loading && items.length && !matched.length ? <div className="dir-empty">No conversations match “{query.trim()}”</div> : null}
        {groups.map(([label, list]) => (
          <section key={label}>
            <h3>{label}</h3>
            {list.map((item) => (
              <button
                type="button"
                key={item.id}
                className={`conv-row ${value === item.id ? 'selected' : ''}`}
                aria-pressed={value === item.id}
                disabled={item.live}
                data-tip={item.live ? 'Already open in a live session' : undefined}
                onClick={() => onChange(value === item.id ? '' : item.id)}
              >
                <History size={15} className="conv-icon" />
                <span className="conv-text">
                  <span className="conv-title clamp">{conversationTitle(item)}</span>
                  <span className="conv-sub clamp">
                    {item.updated_at ? messageClock(item.updated_at) : item.id} · {item.message_count} msgs
                    {item.name && item.last_user_message ? ` · ${item.last_user_message}` : ''}
                  </span>
                </span>
                {item.live ? <span className="pill-mini">live</span> : null}
              </button>
            ))}
          </section>
        ))}
        {matched.length > CONVERSATION_LIMIT ? (
          <div className="dir-empty">{matched.length - CONVERSATION_LIMIT} older conversations not shown — search to find them</div>
        ) : null}
      </div>
    </div>
  )
}

export function NewSessionDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (session: SessionSummary) => void }) {
  const [last] = useState(remembered)
  const [cwd, setCwd] = useState(last.cwd ?? '')
  const [name, setName] = useState('')
  const [model, setModel] = useState(last.model ?? '')
  const [persona, setPersona] = useState(last.persona ?? '')
  const [effort, setEffort] = useState(last.reasoning_effort ?? '')
  const [resume, setResume] = useState('')
  const [recents, setRecents] = useState<string[] | null>(null)
  const [options, setOptions] = useState<SessionOptions | null>(null)
  const [saved, setSaved] = useState<ConversationSummary[]>([])
  // Requests for the typed folder's options and conversations still running.
  const [checking, setChecking] = useState(0)
  const [loadingSaved, setLoadingSaved] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // Latest picks, read when a folder's options arrive.
  const selection = useRef({ model, persona, effort })
  selection.current = { model, persona, effort }

  useEffect(() => {
    recentWorkspaces()
      .then((list) => {
        setRecents(list)
        setCwd((current) => current || list[0] || '')
      })
      .catch(() => setRecents([]))
  }, [])

  useEffect(() => {
    // Saved conversations belong to one folder: forget the list and the pick.
    setSaved([])
    setResume('')
    if (!absolute(cwd)) return
    let cancelled = false
    setChecking(2)
    setLoadingSaved(true)
    const done = () => !cancelled && setChecking((value) => Math.max(0, value - 1))
    const timer = window.setTimeout(() => {
      sessionOptions(cwd)
        .then((data) => {
          if (cancelled) return
          setOptions(data)
          const pick = (current: string, list: string[], fallback: string) => (current && list.includes(current) ? current : fallback)
          const current = selection.current
          const nextModel = pick(current.model, data.models, data.default_model)
          setModel(nextModel)
          // Persona and reasoning are always explicit, never a hidden default.
          setPersona(pick(current.persona, data.personas, data.personas.includes(data.default_persona) ? data.default_persona : data.personas[0] ?? ''))
          setEffort(pick(current.effort, data.reasoning_efforts, data.default_reasoning_efforts?.[nextModel] ?? 'high'))
        })
        .catch(() => !cancelled && setOptions(null))
        .finally(done)
      conversations(cwd)
        .then((list) => !cancelled && setSaved(list))
        .catch(() => !cancelled && setSaved([]))
        .finally(() => {
          done()
          if (!cancelled) setLoadingSaved(false)
        })
    }, SETTLE_MS)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      setChecking(0)
      setLoadingSaved(false)
    }
  }, [cwd])

  const changeModel = (next: string) => {
    setModel(next)
    // Each model starts at its own configured effort, as in the CLI.
    const preferred = options?.default_reasoning_efforts?.[next]
    if (preferred) setEffort(preferred)
  }

  const submit = async () => {
    if (busy || !cwd.trim()) return
    setBusy(true)
    setError('')
    try {
      const session = await createSession({ cwd: cwd.trim(), name: name.trim(), model, persona, reasoning_effort: effort, resume })
      store.set(LAST, JSON.stringify({ cwd: cwd.trim(), model, persona, reasoning_effort: effort }))
      onCreated(session)
    } catch (exc) {
      setError(errorMessage(exc, 'Could not start the session.'))
    } finally {
      setBusy(false)
    }
  }

  const choices = (list: string[] | undefined, current: string) => (list?.length ? list : current ? [current] : [])
  const loadingNote = checking ? <span className="field-note"><Spinner size={11} />loading…</span> : null
  const picked = saved.find((item) => item.id === resume)
  return (
    <Modal onClose={busy ? () => undefined : onClose} className="new-session" label="New session">
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="modal-head">
          <Rocket size={18} />
          <h2>New session</h2>
          <span className="grow" />
          <IconButton icon={X} size="sm" label="Close" onClick={onClose} disabled={busy} />
        </div>

        <div className="ns-body">
          <div className="ns-col">
            <label className="field">
              <span className="field-label"><FolderOpen size={14} />Working directory</span>
              <input
                {...NO_AUTOFILL}
                value={cwd}
                onChange={(event) => setCwd(event.target.value)}
                placeholder="/path/to/project"
                className="mono"
                autoFocus
                spellCheck={false}
              />
            </label>
            {recents === null ? <LoadingHint className="recents">Loading recent folders…</LoadingHint> : null}
            {recents?.length ? (
              <div className="recents">
                {recents.slice(0, 8).map((path) => (
                  <button type="button" key={path} className={`chip ${path === cwd ? 'selected' : ''}`} onClick={() => setCwd(path)} data-tip={path}>
                    <Clock size={11} />{basename(path)}
                  </button>
                ))}
              </div>
            ) : null}
            <DirectoryPicker path={cwd} onNavigate={setCwd} />
          </div>

          <div className="ns-col">
            <label className="field">
              <span className="field-label"><Tag size={14} />Name</span>
              <input {...NO_AUTOFILL} value={name} onChange={(event) => setName(event.target.value)} placeholder={resume ? 'keep the saved name' : 'optional'} />
            </label>
            <label className="field">
              <span className="field-label"><Cpu size={14} />Model{loadingNote}</span>
              <select value={model} onChange={(event) => changeModel(event.target.value)}>
                {choices(options?.models, model).map((item) => <option key={item} value={item}>{item}</option>)}
                {!options?.models.length && !model ? <option value="">default</option> : null}
              </select>
            </label>
            <div className="field-grid">
              <label className="field">
                <span className="field-label"><UserRound size={14} />Persona{resume ? <span className="field-note">resume keeps its own</span> : null}</span>
                <select value={persona} onChange={(event) => setPersona(event.target.value)} disabled={!!resume}>
                  {choices(options?.personas, persona).map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
              <label className="field">
                <span className="field-label"><Brain size={14} />Reasoning</span>
                <select value={effort} onChange={(event) => setEffort(event.target.value)}>
                  {choices(options?.reasoning_efforts, effort).map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              </label>
            </div>
            <div className="field conv-field">
              <span className="field-label">
                <History size={14} />Conversation
                <span className="field-note clamp">{picked ? `resume “${conversationTitle(picked)}”` : 'start fresh'}</span>
              </span>
              <ConversationPicker key={cwd} items={saved} value={resume} loading={loadingSaved} onChange={setResume} />
            </div>
          </div>
        </div>

        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !cwd.trim()}>
            {busy ? <><Spinner size={14} />Starting…</> : <><Rocket size={15} />{resume ? 'Resume' : 'Start'}</>}
          </button>
        </div>
      </form>
    </Modal>
  )
}
