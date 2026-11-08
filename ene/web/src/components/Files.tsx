import {
  AtSign,
  ChevronRight,
  Eye,
  EyeOff,
  File,
  FileCode,
  FileImage,
  FileText,
  Folder,
  FolderGit2,
  GitBranch,
  House,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { errorMessage, listChanges, listFiles, rawFileUrl, readFile } from '../lib/api'
import { bytes, relativeTime } from '../lib/format'
import { highlight, languageForPath } from '../lib/highlight'
import type { DirListing, FilePreview, GitChanges } from '../lib/protocol'
import { store } from '../lib/storage'
import { CopyButton, Markdown } from './Markdown'
import { Empty, IconButton, Modal, Spinner } from './ui'

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|ico|avif|svg)$/i
const CODE_EXT = /\.(py|pyi|js|jsx|ts|tsx|mjs|cjs|json|ya?ml|toml|ini|cfg|sh|bash|zsh|rs|go|java|c|h|cc|cpp|hpp|cu|css|scss|html|xml|sql|lua|rb|php|swift|kt)$/i

export function fileIcon(name: string, type: 'dir' | 'file') {
  if (type === 'dir') return Folder
  if (IMAGE_EXT.test(name)) return FileImage
  if (CODE_EXT.test(name)) return FileCode
  if (/\.(md|txt|rst|log|csv)$/i.test(name)) return FileText
  return File
}

const STATUS_LETTER: Record<string, string> = {
  modified: 'M', added: 'A', deleted: 'D', renamed: 'R', copied: 'C', conflict: 'U', untracked: '?',
}

export function FileBrowser({
  sessionId,
  onOpen,
  refreshKey,
}: {
  sessionId: string
  onOpen: (path: string) => void
  refreshKey: number
}) {
  const [path, setPath] = useState(() => store.get(`ene.files.${sessionId}`) ?? '')
  const [listing, setListing] = useState<DirListing | null>(null)
  const [changes, setChanges] = useState<GitChanges | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showHidden, setShowHidden] = useState(() => store.get('ene.files.hidden') === '1')
  const [changesOnly, setChangesOnly] = useState(false)
  const [filter, setFilter] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    listFiles(sessionId, path)
      .then((data) => {
        if (cancelled) return
        setListing(data)
        store.set(`ene.files.${sessionId}`, data.path)
      })
      .catch((exc) => {
        if (cancelled) return
        // A remembered folder that disappeared falls back to the root.
        if (path) setPath('')
        else setError(errorMessage(exc, 'Could not list files.'))
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [sessionId, path, tick, refreshKey])

  useEffect(() => {
    let cancelled = false
    listChanges(sessionId)
      .then((data) => !cancelled && setChanges(data))
      .catch(() => !cancelled && setChanges(null))
    return () => {
      cancelled = true
    }
  }, [sessionId, tick, refreshKey])

  const changeMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const change of changes?.changes ?? []) map.set(change.path, change.status)
    return map
  }, [changes])

  const dirtyDirs = useMemo(() => {
    const dirs = new Set<string>()
    for (const change of changes?.changes ?? []) {
      const parts = change.path.split('/')
      for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join('/'))
    }
    return dirs
  }, [changes])

  const join = useCallback((name: string) => (listing?.path ? `${listing.path}/${name}` : name), [listing])
  const needle = filter.trim().toLowerCase()
  const entries = (listing?.entries ?? []).filter(
    (entry) => (showHidden || !entry.hidden) && (!needle || entry.name.toLowerCase().includes(needle)),
  )
  const crumbs = listing?.path ? listing.path.split('/') : []
  const changeCount = changes?.changes.length ?? 0

  return (
    <div className="files">
      <div className="panel-toolbar">
        <div className="crumbs" role="navigation" aria-label="Folder path">
          <button type="button" onClick={() => setPath('')} data-tip={listing?.root ?? 'Workspace'} aria-label="Workspace root">
            <House size={14} />
          </button>
          {crumbs.map((crumb, index) => (
            <span className="crumb" key={`${index}-${crumb}`}>
              <ChevronRight size={12} />
              <button type="button" onClick={() => setPath(crumbs.slice(0, index + 1).join('/'))}>{crumb}</button>
            </span>
          ))}
        </div>
        <span className="grow" />
        {changes?.git ? (
          <IconButton
            icon={GitBranch}
            size="sm"
            label={changesOnly ? 'Show all files' : `Changed files${changes.branch ? ` on ${changes.branch}` : ''}`}
            active={changesOnly}
            badge={changeCount || null}
            onClick={() => setChangesOnly(!changesOnly)}
          />
        ) : null}
        <IconButton
          icon={showHidden ? Eye : EyeOff}
          size="sm"
          label={showHidden ? 'Hide dotfiles' : 'Show dotfiles'}
          active={showHidden}
          onClick={() => {
            store.set('ene.files.hidden', showHidden ? null : '1')
            setShowHidden(!showHidden)
          }}
        />
        <IconButton icon={RefreshCw} size="sm" label="Refresh" busy={loading} onClick={() => setTick(tick + 1)} />
      </div>
      {!changesOnly ? (
        <label className="filter">
          <Search size={14} />
          <input value={filter} placeholder="Filter this folder" onChange={(event) => setFilter(event.target.value)} />
          {filter ? <button type="button" aria-label="Clear filter" onClick={() => setFilter('')}><X size={13} /></button> : null}
        </label>
      ) : null}
      {error ? <div className="panel-error">{error}</div> : null}
      {changesOnly ? (
        <div className="file-list">
          {changes?.branch ? <div className="list-caption"><FolderGit2 size={13} />{changes.branch}</div> : null}
          {changeCount === 0 ? <Empty icon={GitBranch}>Working tree clean.</Empty> : null}
          {changes?.changes.map((change) => {
            const name = change.path.split('/').at(-1) ?? change.path
            const Icon = fileIcon(name, 'file')
            return (
              <button
                type="button"
                className="file-row"
                key={change.path}
                disabled={change.status === 'deleted'}
                onClick={() => onOpen(change.path)}
              >
                <Icon size={15} className="file-icon" />
                <span className="file-name">
                  {name}
                  <span className="file-dir">{change.path.slice(0, -name.length)}</span>
                </span>
                <span className={`git-badge ${change.status}`} data-tip={change.status}>{STATUS_LETTER[change.status]}</span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="file-list">
          {listing?.path ? (
            <button type="button" className="file-row up" onClick={() => setPath(crumbs.slice(0, -1).join('/'))}>
              <Folder size={15} className="file-icon" />
              <span className="file-name">..</span>
            </button>
          ) : null}
          {entries.map((entry) => {
            const full = join(entry.name)
            const Icon = fileIcon(entry.name, entry.type)
            const status = entry.type === 'file' ? changeMap.get(full) : undefined
            const dirty = entry.type === 'dir' && dirtyDirs.has(full)
            return (
              <button
                type="button"
                className={`file-row ${entry.hidden ? 'hidden-entry' : ''}`}
                key={entry.name}
                onClick={() => (entry.type === 'dir' ? (setFilter(''), setPath(full)) : onOpen(full))}
                title={relativeTime(entry.mtime)}
              >
                <Icon size={15} className={`file-icon ${entry.type}`} />
                <span className="file-name">{entry.name}{entry.symlink ? <span className="file-dir"> ↪</span> : null}</span>
                {status ? <span className={`git-badge ${status}`} data-tip={status}>{STATUS_LETTER[status]}</span> : null}
                {dirty ? <span className="dirty-dot" data-tip="Contains changes" /> : null}
                {entry.type === 'file' ? <span className="file-size">{bytes(entry.size)}</span> : null}
              </button>
            )
          })}
          {listing && entries.length === 0 ? <Empty icon={Folder}>{needle ? 'No matches.' : 'Empty folder.'}</Empty> : null}
          {listing?.truncated ? <div className="list-caption">Showing the first 2000 entries.</div> : null}
          {!listing && loading ? <div className="center-pad"><Spinner /></div> : null}
        </div>
      )}
    </div>
  )
}

export function FileViewer({
  sessionId,
  path,
  onClose,
  onMention,
}: {
  sessionId: string
  path: string
  onClose: () => void
  onMention: (path: string) => void
}) {
  const [file, setFile] = useState<FilePreview | null>(null)
  const [error, setError] = useState('')
  const [rendered, setRendered] = useState(/\.md$/i.test(path))
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setError('')
    readFile(sessionId, path)
      .then((data) => !cancelled && setFile(data))
      .catch((exc) => !cancelled && setError(errorMessage(exc, 'Could not open the file.')))
    return () => {
      cancelled = true
    }
  }, [sessionId, path, tick])

  const language = languageForPath(path)
  const text = file?.kind === 'text' ? file.text ?? '' : ''
  const html = useMemo(() => (text ? highlight(text, language) : null), [text, language])
  const lineCount = text ? text.split('\n').length : 0
  const gutter = useMemo(() => Array.from({ length: lineCount }, (_, i) => i + 1).join('\n'), [lineCount])
  const isMarkdown = /\.(md|markdown)$/i.test(path)
  const Icon = fileIcon(path, 'file')

  return (
    <Modal onClose={onClose} className="viewer" label={`File ${path}`}>
      <div className="viewer-head">
        <Icon size={16} />
        <div className="viewer-title">
          <span className="mono clamp">{path}</span>
          {file ? <span className="muted small">{bytes(file.size)} · {relativeTime(file.mtime)}{file.kind === 'text' ? ` · ${lineCount} lines` : ''}</span> : null}
        </div>
        <span className="grow" />
        {isMarkdown && file?.kind === 'text' ? (
          <IconButton icon={rendered ? FileCode : FileText} size="sm" label={rendered ? 'Show source' : 'Render markdown'} onClick={() => setRendered(!rendered)} />
        ) : null}
        <IconButton icon={AtSign} size="sm" label="Mention in message" onClick={() => onMention(path)} />
        <CopyButton text={path} label="Copy path" />
        {file?.kind === 'text' ? <CopyButton text={text} label="Copy contents" /> : null}
        <IconButton icon={RefreshCw} size="sm" label="Reload" onClick={() => setTick(tick + 1)} />
        <IconButton icon={X} size="sm" label="Close (Esc)" onClick={onClose} />
      </div>
      <div className="viewer-body">
        {error ? <div className="panel-error">{error}</div> : null}
        {!file && !error ? <div className="center-pad"><Spinner /></div> : null}
        {file?.kind === 'image' ? (
          <div className="viewer-image"><img src={rawFileUrl(sessionId, path)} alt={path} /></div>
        ) : null}
        {file?.kind === 'binary' ? <Empty icon={File}>Binary file ({file.mime}) — no preview.</Empty> : null}
        {file?.kind === 'text' && rendered && isMarkdown ? (
          <div className="viewer-markdown"><Markdown text={text} /></div>
        ) : null}
        {file?.kind === 'text' && !(rendered && isMarkdown) ? (
          <div className="code-view">
            <pre className="gutter" aria-hidden>{gutter}</pre>
            {html !== null
              ? <pre className="code"><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
              : <pre className="code"><code>{text}</code></pre>}
          </div>
        ) : null}
        {file?.truncated ? <div className="list-caption">Preview truncated at 512 KB.</div> : null}
      </div>
    </Modal>
  )
}
