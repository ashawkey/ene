import { ArrowUp, Brain, Check, Clock, Cpu, Eye, EyeOff, Folder, FolderOpen, History, Rocket, Tag, UserRound, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { browseDirectories, conversations, createSession, errorMessage, recentWorkspaces, sessionOptions } from '../lib/api'
import { basename } from '../lib/format'
import type { ConversationSummary, FsListing, SessionOptions, SessionSummary } from '../lib/protocol'
import { store } from '../lib/storage'
import { IconButton, Modal, Spinner } from './ui'

const LAST = 'ene.new.last'

type Remembered = { cwd?: string; model?: string; persona?: string; reasoning_effort?: string }

function remembered(): Remembered {
  try {
    return JSON.parse(store.get(LAST) ?? '{}') as Remembered
  } catch {
    return {}
  }
}

function DirectoryPicker({ path, onPick }: { path: string; onPick: (path: string) => void }) {
  const [listing, setListing] = useState<FsListing | null>(null)
  const [error, setError] = useState('')
  const [hidden, setHidden] = useState(false)
  const [target, setTarget] = useState(path)
  useEffect(() => {
    let cancelled = false
    browseDirectories(target)
      .then((data) => {
        if (!cancelled) {
          setListing(data)
          setError('')
        }
      })
      .catch((exc) => {
        if (cancelled) return
        if (target) setTarget('')
        else setError(errorMessage(exc, 'Cannot list this folder.'))
      })
    return () => {
      cancelled = true
    }
  }, [target])
  return (
    <div className="dir-picker">
      <div className="panel-toolbar">
        <IconButton icon={ArrowUp} size="sm" label="Parent folder" disabled={!listing?.parent} onClick={() => listing && setTarget(listing.parent)} />
        <span className="mono small clamp grow">{listing?.path ?? '…'}</span>
        <IconButton icon={hidden ? Eye : EyeOff} size="sm" label={hidden ? 'Hide dot-folders' : 'Show dot-folders'} active={hidden} onClick={() => setHidden(!hidden)} />
        <IconButton icon={Check} size="sm" tone="accent" label="Use this folder" disabled={!listing} onClick={() => listing && onPick(listing.path)} />
      </div>
      {error ? <div className="panel-error">{error}</div> : null}
      <div className="dir-list">
        {listing?.entries.filter((entry) => hidden || !entry.hidden).map((entry) => (
          <button type="button" key={entry.path} className="file-row" onClick={() => setTarget(entry.path)} onDoubleClick={() => onPick(entry.path)}>
            <Folder size={15} className="file-icon dir" />
            <span className="file-name">{entry.name}</span>
          </button>
        ))}
        {!listing && !error ? <div className="center-pad"><Spinner /></div> : null}
      </div>
    </div>
  )
}

export function NewSessionDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (session: SessionSummary) => void }) {
  const last = remembered()
  const [cwd, setCwd] = useState(last.cwd ?? '')
  const [name, setName] = useState('')
  const [model, setModel] = useState(last.model ?? '')
  const [persona, setPersona] = useState(last.persona ?? '')
  const [effort, setEffort] = useState(last.reasoning_effort ?? '')
  const [resume, setResume] = useState('')
  const [recents, setRecents] = useState<string[]>([])
  const [options, setOptions] = useState<SessionOptions | null>(null)
  const [saved, setSaved] = useState<ConversationSummary[]>([])
  const [browsing, setBrowsing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    recentWorkspaces()
      .then((list) => {
        setRecents(list)
        setCwd((current) => current || list[0] || '')
      })
      .catch(() => setRecents([]))
  }, [])

  useEffect(() => {
    if (!cwd.startsWith('/') && !cwd.startsWith('~') && !/^[A-Za-z]:[\\/]/.test(cwd)) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      sessionOptions(cwd)
        .then((data) => {
          if (cancelled) return
          setOptions(data)
          setModel((current) => (current && data.models.includes(current) ? current : data.default_model))
          setPersona((current) => (current && data.personas.includes(current) ? current : ''))
        })
        .catch(() => !cancelled && setOptions(null))
      conversations(cwd)
        .then((list) => !cancelled && setSaved(list))
        .catch(() => !cancelled && setSaved([]))
    }, 250)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [cwd])

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

        <label className="field">
          <span className="field-label"><FolderOpen size={14} />Working directory</span>
          <div className="input-row">
            <input
              value={cwd}
              onChange={(event) => setCwd(event.target.value)}
              placeholder="/path/to/project"
              className="mono"
              autoFocus
              spellCheck={false}
            />
            <IconButton icon={Folder} label="Browse" active={browsing} onClick={() => setBrowsing(!browsing)} />
          </div>
        </label>
        {browsing ? <DirectoryPicker path={cwd} onPick={(path) => { setCwd(path); setBrowsing(false) }} /> : null}
        {recents.length ? (
          <div className="recents">
            {recents.slice(0, 8).map((path) => (
              <button type="button" key={path} className={`chip ${path === cwd ? 'selected' : ''}`} onClick={() => setCwd(path)} data-tip={path}>
                <Clock size={11} />{basename(path)}
              </button>
            ))}
          </div>
        ) : null}

        <div className="field-grid">
          <label className="field">
            <span className="field-label"><Tag size={14} />Name</span>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="optional" />
          </label>
          <label className="field">
            <span className="field-label"><Cpu size={14} />Model</span>
            <select value={model} onChange={(event) => setModel(event.target.value)}>
              {(options?.models ?? (model ? [model] : [])).map((item) => <option key={item} value={item}>{item}</option>)}
              {!options?.models.length ? <option value="">default</option> : null}
            </select>
          </label>
          <label className="field">
            <span className="field-label"><UserRound size={14} />Persona</span>
            <select value={persona} onChange={(event) => setPersona(event.target.value)}>
              <option value="">default</option>
              {(options?.personas ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field-label"><Brain size={14} />Reasoning</span>
            <select value={effort} onChange={(event) => setEffort(event.target.value)}>
              <option value="">default</option>
              {(options?.reasoning_efforts ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
        </div>

        <label className="field">
          <span className="field-label"><History size={14} />Resume</span>
          <select value={resume} onChange={(event) => setResume(event.target.value)}>
            <option value="">New conversation</option>
            {saved.map((item) => (
              <option key={item.id} value={item.id} disabled={item.live}>
                {(item.name || item.last_user_message || item.id).slice(0, 70)} · {item.message_count} msgs{item.live ? ' (live)' : ''}
              </option>
            ))}
          </select>
        </label>

        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !cwd.trim()}>
            {busy ? <><Spinner size={14} />Starting…</> : <><Rocket size={15} />Start</>}
          </button>
        </div>
      </form>
    </Modal>
  )
}
