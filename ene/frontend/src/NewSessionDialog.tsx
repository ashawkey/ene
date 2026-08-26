import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  listConversations,
  listDirectory,
  listOptions,
  listWorkspaces,
} from './api'
import type { ConversationSummary, FsListing, NewSessionRequest, SessionOptions } from './types'

/**
 * Modal for starting a live session: pick a workspace (browsable, with
 * recents), then optionally a name, model, persona, effort, and a saved
 * conversation to resume.
 */
export function NewSessionDialog({
  onCreate,
  onClose,
}: {
  onCreate: (request: NewSessionRequest) => Promise<void>
  onClose: () => void
}) {
  const [cwd, setCwd] = useState('')
  const [listing, setListing] = useState<FsListing | null>(null)
  const [recents, setRecents] = useState<string[]>([])
  const [showHidden, setShowHidden] = useState(false)
  const [options, setOptions] = useState<SessionOptions | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [name, setName] = useState('')
  const [model, setModel] = useState('')
  const [persona, setPersona] = useState('coder')
  const [effort, setEffort] = useState('high')
  const [resume, setResume] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const dialog = useRef<HTMLDivElement>(null)

  const browse = useCallback(async (path: string) => {
    try {
      const next = await listDirectory(path)
      setListing(next)
      setCwd(next.path)
      setError('')
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not read that directory.')
    }
  }, [])

  useEffect(() => {
    void browse('')
    listWorkspaces().then(setRecents).catch(() => setRecents([]))
  }, [browse])

  // Model/persona/conversation choices are workspace-scoped: project personas
  // and saved conversations both live under the selected directory.
  useEffect(() => {
    if (!cwd) return
    let cancelled = false
    listOptions(cwd)
      .then((next) => {
        if (cancelled) return
        setOptions(next)
        setModel((current) => current || next.default_model)
      })
      .catch(() => undefined)
    listConversations(cwd)
      .then((next) => {
        if (!cancelled) setConversations(next)
      })
      .catch(() => {
        if (!cancelled) setConversations([])
      })
    setResume('')
    return () => {
      cancelled = true
    }
  }, [cwd])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (submitting) return
    if (!cwd.trim()) {
      setError('Choose a working directory.')
      return
    }
    setSubmitting(true)
    setError('')
    try {
      await onCreate({
        cwd: cwd.trim(),
        name: name.trim(),
        model,
        persona,
        reasoning_effort: effort,
        resume,
      })
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : 'Could not start the session.')
      setSubmitting(false)
    }
  }

  const entries = (listing?.entries ?? []).filter((entry) => showHidden || !entry.hidden)

  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label="New session" ref={dialog}>
        <form onSubmit={submit}>
          <header className="modal-header">
            <h2>New session</h2>
            <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
              ×
            </button>
          </header>

          <label className="field">
            <span>Working directory</span>
            <input
              type="text"
              value={cwd}
              onChange={(event) => setCwd(event.target.value)}
              onBlur={(event) => void browse(event.target.value)}
              spellCheck={false}
            />
          </label>

          {recents.length > 0 ? (
            <div className="recents">
              {recents.slice(0, 6).map((path) => (
                <button key={path} type="button" className="recent" onClick={() => void browse(path)}>
                  {path.split(/[\\/]/).filter(Boolean).pop() || path}
                </button>
              ))}
            </div>
          ) : null}

          <div className="browser">
            <div className="browser-toolbar">
              <button
                type="button"
                onClick={() => listing?.parent && void browse(listing.parent)}
                disabled={!listing?.parent}
              >
                ↑ Up
              </button>
              <label className="hidden-toggle">
                <input
                  type="checkbox"
                  checked={showHidden}
                  onChange={(event) => setShowHidden(event.target.checked)}
                />
                Show hidden
              </label>
            </div>
            <ul className="browser-list">
              {entries.length === 0 ? (
                <li className="browser-empty">No subdirectories</li>
              ) : (
                entries.map((entry) => (
                  <li key={entry.path}>
                    <button type="button" onClick={() => void browse(entry.path)}>
                      {entry.name}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>

          <div className="field-row">
            <label className="field">
              <span>Name</span>
              <input
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="optional"
              />
            </label>
            <label className="field">
              <span>Model</span>
              <select value={model} onChange={(event) => setModel(event.target.value)}>
                {(options?.models ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="field-row">
            <label className="field">
              <span>Persona</span>
              <select value={persona} onChange={(event) => setPersona(event.target.value)}>
                {(options?.personas ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Reasoning effort</span>
              <select value={effort} onChange={(event) => setEffort(event.target.value)}>
                {(options?.reasoning_efforts ?? []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="field">
            <span>Resume conversation</span>
            <select value={resume} onChange={(event) => setResume(event.target.value)}>
              <option value="">start a new conversation</option>
              {conversations.map((item) => (
                <option key={item.id} value={item.id} disabled={item.live}>
                  {`${item.name || item.id} · ${item.message_count} msgs${item.live ? ' · live' : ''}`}
                </option>
              ))}
            </select>
          </label>

          {error ? <p className="modal-error" role="alert">{error}</p> : null}

          <footer className="modal-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="primary" disabled={submitting}>
              {submitting ? 'Starting…' : 'Start session'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  )
}
