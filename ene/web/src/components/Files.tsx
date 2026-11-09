import {
  AtSign,
  ChevronRight,
  Eye,
  EyeOff,
  File,
  FileCode,
  FileDiff,
  FileImage,
  FileText,
  Folder,
  FolderGit2,
  GitBranch,
  House,
  Link,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { memo, useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react'

import { errorMessage, fileDiff, listChanges, listFiles, rawFileUrl, readFile } from '../lib/api'
import { bytes, relativeTime } from '../lib/format'
import { highlight, languageForPath, splitHighlightedLines } from '../lib/highlight'
import type { DirListing, FilePreview, GitChanges, GitDiff } from '../lib/protocol'
import { splitLines } from '../lib/splitDiff'
import { store } from '../lib/storage'
import { CopyButton, Markdown } from './Markdown'
import { SplitDiff } from './SplitDiff'
import { Empty, IconButton, Modal, Spinner } from './ui'
import { NO_AUTOFILL } from '../lib/noAutofill'

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

/** Lines per lazily painted block of the code view. */
const CODE_CHUNK = 200

// Memoized: the inspector re-renders on every streamed update, and a large
// folder listing must not re-render with it.
export const FileBrowser = memo(function FileBrowser({
  sessionId,
  onOpen,
  refreshKey,
}: {
  sessionId: string
  onOpen: (path: string, changed?: boolean) => void
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
  // Typing stays responsive while a long listing re-filters in the background.
  const needle = useDeferredValue(filter).trim().toLowerCase()
  const entries = useMemo(
    () => (listing?.entries ?? []).filter(
      (entry) => (showHidden || !entry.hidden) && (!needle || entry.name.toLowerCase().includes(needle)),
    ),
    [listing, showHidden, needle],
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
          <input {...NO_AUTOFILL} value={filter} placeholder="Filter this folder" onChange={(event) => setFilter(event.target.value)} />
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
                onClick={() => onOpen(change.path, true)}
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
                onClick={() => (entry.type === 'dir' ? (setFilter(''), setPath(full)) : onOpen(full, status ? true : undefined))}
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
})

export const FileViewer = memo(function FileViewer({
  sessionId,
  path,
  changed,
  onClose,
  onMention,
}: {
  sessionId: string
  path: string
  /** True: open on the diff; undefined: probe Git and offer the diff if changed. */
  changed?: boolean
  onClose: () => void
  onMention: (path: string) => void
}) {
  const [file, setFile] = useState<FilePreview | null>(null)
  const [error, setError] = useState('')
  const [diff, setDiff] = useState<GitDiff | null>(null)
  const [diffError, setDiffError] = useState('')
  const [mode, setMode] = useState<'diff' | 'file'>(changed ? 'diff' : 'file')
  const [rendered, setRendered] = useState(/\.md$/i.test(path))
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setError('')
    readFile(sessionId, path)
      .then((data) => !cancelled && setFile(data))
      .catch((exc) => !cancelled && setError(errorMessage(exc, 'Could not open the file.')))
    fileDiff(sessionId, path)
      .then((data) => {
        if (cancelled) return
        setDiff(data)
        setDiffError('')
        // A deleted file has nothing else to show.
        if (data.status === 'deleted') setMode('diff')
      })
      .catch((exc) => {
        if (cancelled) return
        setDiff(null)
        setDiffError(errorMessage(exc, 'No Git diff available.'))
        setMode('file')
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, path, tick])

  const language = languageForPath(path)
  const text = file?.kind === 'text' ? file.text ?? '' : ''
  // One row per line so long lines wrap without losing their line number.
  const lines = useMemo(() => {
    if (!text) return [] as Array<{ html: string | null; text: string }>
    const plain = text.split('\n')
    const html = highlight(text, language)
    const marked = html !== null ? splitHighlightedLines(html) : null
    return plain.map((line, index) => ({ html: marked?.[index] ?? null, text: line }))
  }, [text, language])
  const lineCount = lines.length
  const isMarkdown = /\.(md|markdown)$/i.test(path)
  const Icon = fileIcon(path, 'file')
  const hasDiff = diff !== null && diff.status !== 'unchanged'
  const showDiff = mode === 'diff' && (hasDiff || changed)
  // Diffed once: the stat and the split view share the same rows.
  const diffRows = useMemo(
    () => (diff && !diff.binary && !diff.too_large ? splitLines(diff.old, diff.new) : null),
    [diff],
  )
  const stat = useMemo(() => {
    if (!diffRows) return null
    return {
      added: diffRows.filter((line) => line.right?.kind === 'add').length,
      removed: diffRows.filter((line) => line.left?.kind === 'del').length,
    }
  }, [diffRows])
  const chunks = useMemo(() => {
    const result: Array<{ start: number; lines: typeof lines }> = []
    for (let start = 0; start < lines.length; start += CODE_CHUNK) {
      result.push({ start, lines: lines.slice(start, start + CODE_CHUNK) })
    }
    return result
  }, [lines])

  return (
    <Modal onClose={onClose} className="viewer" label={`File ${path}`}>
      <div className="viewer-head">
        <Icon size={16} />
        <div className="viewer-title">
          <span className="mono clamp">{path}</span>
          <span className="muted small">
            {showDiff && diff
              ? <>{diff.status} vs HEAD{stat ? <> · <b className="ok">+{stat.added}</b> <b className="fail">−{stat.removed}</b></> : null}</>
              : file ? `${bytes(file.size)} · ${relativeTime(file.mtime)}${file.kind === 'text' ? ` · ${lineCount} lines` : ''}` : ''}
          </span>
        </div>
        <span className="grow" />
        {hasDiff ? (
          <div className="segmented" role="tablist" aria-label="View">
            <button type="button" role="tab" aria-selected={showDiff} className={showDiff ? 'on' : ''} onClick={() => setMode('diff')} data-tip="Changes vs HEAD">
              <FileDiff size={14} />
            </button>
            <button type="button" role="tab" aria-selected={!showDiff} className={!showDiff ? 'on' : ''} disabled={diff?.status === 'deleted'} onClick={() => setMode('file')} data-tip="File">
              <FileCode size={14} />
            </button>
          </div>
        ) : null}
        {!showDiff && isMarkdown && file?.kind === 'text' ? (
          <IconButton icon={rendered ? FileCode : FileText} size="sm" label={rendered ? 'Show source' : 'Render markdown'} onClick={() => setRendered(!rendered)} />
        ) : null}
        <IconButton icon={AtSign} size="sm" label="Mention in message" onClick={() => onMention(path)} />
        <CopyButton text={path} label="Copy path" icon={Link} />
        {file?.kind === 'text' ? <CopyButton text={text} label="Copy contents" /> : null}
        <IconButton icon={RefreshCw} size="sm" label="Reload" onClick={() => setTick(tick + 1)} />
        <IconButton icon={X} size="sm" label="Close (Esc)" onClick={onClose} />
      </div>
      <div className="viewer-body">
        {showDiff ? (
          diff === null
            ? (diffError ? <div className="panel-error">{diffError}</div> : <div className="center-pad"><Spinner /></div>)
            : diff.binary
              ? <Empty icon={File}>Binary file changed.</Empty>
              : diff.too_large
                ? <Empty icon={File}>Too large to diff.</Empty>
                : <SplitDiff path={path} oldText={diff.old} newText={diff.new} lines={diffRows ?? undefined} />
        ) : (
          <>
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
                {/* Chunks off screen skip layout and paint (content-visibility). */}
                {chunks.map((chunk) => (
                  <div className="code-chunk" key={chunk.start}>
                    {chunk.lines.map((line, offset) => (
                      <div className="code-line" key={offset}>
                        <span className="ln" aria-hidden>{chunk.start + offset + 1}</span>
                        {line.html !== null
                          ? <code className="hljs" dangerouslySetInnerHTML={{ __html: line.html || ' ' }} />
                          : <code>{line.text || ' '}</code>}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : null}
            {file?.truncated ? <div className="list-caption">Preview truncated at 512 KB.</div> : null}
          </>
        )}
      </div>
    </Modal>
  )
})
