import {
  ArrowDownAZ,
  ArrowDownWideNarrow,
  ArrowLeft,
  AtSign,
  BookOpen,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock,
  Copy,
  Download,
  Ellipsis,
  Eye,
  EyeOff,
  File,
  FileCode,
  FileDiff,
  FileImage,
  FileText,
  FlaskConical,
  Folder,
  FolderGit2,
  FolderOpen,
  FolderTree,
  GitBranch,
  History,
  House,
  Link,
  ListTree,
  MessageSquareCode,
  RefreshCw,
  Search,
  SquareTerminal,
  TextSearch as TextSearchIcon,
  WrapText,
  X,
} from 'lucide-react'
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import { ApiError, downloadUrl, errorMessage, fileDiff, fileLog, findFiles, listChanges, listFiles, rawFileUrl, readFile } from '../lib/api'
import { type LineMatch, canHighlight, findInElement, findInLines, gotoLine, lineRef, lineSpan, textRange } from '../lib/codeFind'
import { bytes, relativeTime } from '../lib/format'
import { highlight, languageForPath, splitHighlightedLines } from '../lib/highlight'
import { MOD_KEY, hasShortcutModifier } from '../lib/platform'
import type { DirEntry, DirListing, FilePreview, FindResult, FoundPath, GitChange, GitChanges, GitCommit, GitDiff, GitLog } from '../lib/protocol'
import { type FilesFocus, type Range, QueryCache, narrowMatches, pathMatchRanges, useDebounced, useFilesFocus } from '../lib/search'
import { type SplitLine, splitLines } from '../lib/splitDiff'
import { store } from '../lib/storage'
import { toast } from '../lib/toast'
import { ContextMenu, type MenuEntry, useContextMenu } from './ContextMenu'
import { CopyButton, Markdown } from './Markdown'
import { Highlighted, TextSearch } from './Search'
import { SplitDiff } from './SplitDiff'
import { Empty, IconButton, Modal, Spinner, copyText } from './ui'
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
/** Folders whose expansion is remembered per session. */
const MAX_REMEMBERED_OPEN = 200

/** Which viewer tab to open on; the default picks the file or its diff. */
export type FileView = 'history'
type OpenFile = (path: string, changed?: boolean, line?: number, view?: FileView) => void

/** A list row that opens a path, with hover buttons to mention it and open its actions menu. */
export function FileItem({
  path,
  className = '',
  title,
  onClick,
  onMention,
  onMenu,
  expanded,
  depth,
  attrs,
  children,
}: {
  /** Workspace-relative path; folders end with ``/`` so the mention reads as one. */
  path: string
  className?: string
  title?: string
  onClick: () => void
  onMention: (path: string) => void
  /** Opens the actions menu (right click, the context-menu key, or the ⋯ button). */
  onMenu?: (event: ReactMouseEvent<HTMLElement>) => void
  /** Set for rows that fold a group (search results, tree folders). */
  expanded?: boolean
  /** Indentation level in the tree. */
  depth?: number
  /** Data attributes for keyboard navigation. */
  attrs?: Record<`data-${string}`, string>
  children: ReactNode
}) {
  return (
    <div
      className={`file-item ${className}`}
      style={depth ? { paddingLeft: depth * 14 } : undefined}
      onContextMenu={onMenu}
    >
      <button type="button" className="file-row" onClick={onClick} title={title} aria-expanded={expanded} {...attrs}>
        {children}
      </button>
      <span className="row-actions">
        <button
          type="button"
          className="copy-btn"
          aria-label={`Mention ${path} in message`}
          data-tip="Mention in message"
          onClick={() => onMention(path)}
        >
          <AtSign size={14} />
        </button>
        {onMenu ? (
          <button type="button" className="copy-btn" aria-label={`Actions for ${path}`} data-tip="More actions" onClick={onMenu}>
            <Ellipsis size={14} />
          </button>
        ) : null}
      </span>
    </div>
  )
}

const isHiddenPath = (path: string) => path.split('/').some((part) => part.startsWith('.'))
const parentOf = (path: string) => path.split('/').slice(0, -1).join('/')
const baseName = (path: string) => path.split('/').at(-1) ?? path

type SortKey = 'name' | 'mtime' | 'size'
const SORTS: Record<SortKey, { label: string; icon: typeof Clock; next: SortKey }> = {
  name: { label: 'Sorted by name', icon: ArrowDownAZ, next: 'mtime' },
  mtime: { label: 'Sorted by last modified', icon: Clock, next: 'size' },
  size: { label: 'Sorted by size', icon: ArrowDownWideNarrow, next: 'name' },
}
const isSortKey = (value: unknown): value is SortKey => value === 'name' || value === 'mtime' || value === 'size'

/** Folders first; the hub already lists by name. */
export function sortEntries(entries: DirEntry[], key: SortKey): DirEntry[] {
  if (key === 'name') return entries
  return [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1
    const order = key === 'mtime' ? b.mtime - a.mtime : b.size - a.size
    return order || a.name.toLowerCase().localeCompare(b.name.toLowerCase())
  })
}

function loadOpen(sessionId: string): Set<string> {
  try {
    const value = JSON.parse(store.get(`ene.files.open.${sessionId}`) ?? '[]')
    return new Set(Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [])
  } catch {
    return new Set()
  }
}

/** Download a workspace file through a temporary link (the login cookie authorizes it). */
function download(sessionId: string, path: string) {
  const link = document.createElement('a')
  link.href = downloadUrl(sessionId, path)
  link.download = baseName(path)
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function copyFileContents(sessionId: string, path: string) {
  try {
    const file = await readFile(sessionId, path)
    if (file.kind !== 'text') {
      toast('Only text files can be copied.')
      return
    }
    if (await copyText(file.text ?? '')) toast(file.truncated ? 'Copied the first 512 KB.' : 'Copied the file contents.')
  } catch (exc) {
    toast(errorMessage(exc, 'Could not read the file.'))
  }
}

/** One visible row of the folder tree. */
type TreeRow = { entry: DirEntry; full: string; depth: number; parent: string }

/** Move focus between rows: arrows, Home/End; Right/Left open and close folders. */
function onTreeKey(event: ReactKeyboardEvent<HTMLElement>, toggle: (path: string, open?: boolean) => void) {
  const rows = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('.file-row')]
  const current = document.activeElement as HTMLButtonElement | null
  const index = current ? rows.indexOf(current) : -1
  if (index < 0) return
  const focus = (row: HTMLButtonElement | undefined) => {
    if (!row) return
    event.preventDefault()
    row.focus()
    row.scrollIntoView({ block: 'nearest' })
  }
  const { path, kind, parent } = current!.dataset
  if (event.key === 'ArrowDown') focus(rows[index + 1])
  else if (event.key === 'ArrowUp') focus(rows[index - 1])
  else if (event.key === 'Home') focus(rows[0])
  else if (event.key === 'End') focus(rows.at(-1))
  else if (event.key === 'ArrowRight' && kind === 'dir' && path !== undefined) {
    event.preventDefault()
    if (current!.getAttribute('aria-expanded') === 'true') focus(rows[index + 1])
    else toggle(path, true)
  } else if (event.key === 'ArrowLeft' && path !== undefined) {
    event.preventDefault()
    if (kind === 'dir' && current!.getAttribute('aria-expanded') === 'true') toggle(path, false)
    else if (parent) focus(rows.find((row) => row.dataset.path === parent))
  }
}

// Memoized: the inspector re-renders on every streamed update, and a large
// folder listing must not re-render with it.
// Workspace find answers per session and query, shared across remounts.
const findCache = new QueryCache<FindResult>()

export const FileBrowser = memo(function FileBrowser({
  sessionId,
  onOpen,
  onMention,
  onAsk,
  onTerminal,
  refreshKey,
}: {
  sessionId: string
  onOpen: OpenFile
  onMention: (path: string) => void
  /** Put a prompt in the composer (not sent). */
  onAsk?: (text: string) => void
  /** Open a Web UI terminal in a workspace folder ('' for the root). */
  onTerminal?: (path: string) => void
  refreshKey: number
}) {
  const [path, setPath] = useState(() => store.get(`ene.files.${sessionId}`) ?? '')
  const [listing, setListing] = useState<DirListing | null>(null)
  const [changes, setChanges] = useState<GitChanges | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showHidden, setShowHidden] = useState(() => store.get('ene.files.hidden') === '1')
  const [sort, setSort] = useState<SortKey>(() => {
    const value = store.get('ene.files.sort')
    return isSortKey(value) ? value : 'name'
  })
  const [mode, setMode] = useState<'browse' | 'changes' | 'search'>('browse')
  const [filter, setFilter] = useState('')
  const [tick, setTick] = useState(0)
  // Expanded folders (workspace-relative) and their listings, tagged with the
  // refresh they answer so a refresh reloads every open folder.
  const [open, setOpen] = useState<Set<string>>(() => loadOpen(sessionId))
  const [children, setChildren] = useState<Record<string, { listing: DirListing; generation: string }>>({})
  const pending = useRef(new Set<string>())
  const [revealing, setRevealing] = useState<string | null>(null)
  // Workspace matches and the session and query they answer.
  const [found, setFound] = useState<(FindResult & { session: string; query: string }) | null>(null)
  const [finding, setFinding] = useState(false)
  const [searchFocus, setSearchFocus] = useState(0)
  const filterInput = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  // The requested path of the folder on screen; null until one has listed.
  const listedPath = useRef<string | null>(null)
  const mounted = useRef(true)
  useEffect(() => () => {
    mounted.current = false
  }, [])
  const { menu, open: openMenu, close: closeMenu } = useContextMenu()

  useFilesFocus(useCallback((target: FilesFocus) => {
    if (target === 'search') {
      setMode('search')
      setSearchFocus((value) => value + 1)
    } else {
      setMode('browse')
      // Focus after the browse view has rendered its input.
      window.setTimeout(() => {
        filterInput.current?.focus()
        filterInput.current?.select()
      }, 0)
    }
  }, []))

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    listFiles(sessionId, path)
      .then((data) => {
        if (cancelled) return
        listedPath.current = path
        setListing(data)
        store.set(`ene.files.${sessionId}`, data.path)
      })
      .catch((exc) => {
        if (cancelled) return
        const gone = exc instanceof ApiError && exc.status === 404
        if (path && (listedPath.current === null || (gone && path === listedPath.current))) {
          // The remembered folder, or the one on screen, disappeared: back to the root.
          setPath('')
        } else if (listedPath.current !== null && path !== listedPath.current) {
          // A folder that cannot be opened (outside the workspace, unreadable,
          // offline): say so and stay in the current one.
          toast(errorMessage(exc, 'Could not open the folder.'))
          setPath(listedPath.current)
        } else setError(errorMessage(exc, 'Could not list files.'))
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

  useEffect(() => {
    store.set(`ene.files.open.${sessionId}`, open.size ? JSON.stringify([...open].slice(-MAX_REMEMBERED_OPEN)) : null)
  }, [sessionId, open])

  const root = listing?.path ?? null
  const generation = `${tick}:${refreshKey}`
  // Open folders shown under the current root: every folder between them open too.
  const reachable = useMemo(() => {
    if (root === null) return []
    return [...open].filter((dir) => {
      if (root && !dir.startsWith(`${root}/`)) return false
      const parts = dir.split('/')
      for (let at = (root ? root.split('/').length : 0) + 1; at < parts.length; at++) {
        if (!open.has(parts.slice(0, at).join('/'))) return false
      }
      return true
    })
  }, [open, root])

  useEffect(() => {
    for (const dir of reachable) {
      if (children[dir]?.generation === generation) continue
      const key = `${dir}\u0000${generation}`
      if (pending.current.has(key)) continue
      pending.current.add(key)
      listFiles(sessionId, dir)
        .then((data) => {
          if (mounted.current) setChildren((current) => ({ ...current, [dir]: { listing: data, generation } }))
        })
        .catch((exc) => {
          if (!mounted.current) return
          // Gone since it was opened: just fold it. Anything else deserves a word.
          if (!(exc instanceof ApiError && exc.status === 404)) toast(errorMessage(exc, 'Could not open the folder.'))
          setOpen((current) => {
            const next = new Set(current)
            next.delete(dir)
            return next
          })
        })
        .finally(() => pending.current.delete(key))
    }
    // `children` is read, not watched: answers arriving must not refetch.
  }, [sessionId, reachable, generation])

  const toggle = useCallback((dir: string, wanted?: boolean) => {
    setOpen((current) => {
      const isOpen = current.has(dir)
      if (wanted === isOpen) return current
      const next = new Set(current)
      if (isOpen) next.delete(dir)
      else next.add(dir)
      return next
    })
  }, [])

  // Recursive, fuzzy file finder over the whole workspace (hub-side index).
  const findQuery = useDebounced(filter.trim(), 80)
  // The filter as typed now, for a lookup that Enter is waiting on.
  const typed = useRef(filter)
  typed.current = filter
  const findTick = useRef(tick)
  useEffect(() => {
    if (!findQuery || mode !== 'browse') {
      setFound(null)
      setFinding(false)
      return
    }
    const key = `${sessionId}\u0000${findQuery}`
    // A refresh tick must refetch; otherwise reuse a recent answer.
    const cached = findTick.current === tick ? findCache.get(key) : undefined
    findTick.current = tick
    if (cached) {
      setFound({ ...cached, session: sessionId, query: findQuery })
      setFinding(false)
      return
    }
    let cancelled = false
    setFinding(true)
    findFiles(sessionId, findQuery)
      .then((data) => {
        findCache.set(key, data)
        if (!cancelled) setFound({ ...data, session: sessionId, query: findQuery })
      })
      .catch(() => !cancelled && setFound(null))
      .finally(() => !cancelled && setFinding(false))
    return () => {
      cancelled = true
    }
  }, [sessionId, findQuery, mode, tick])

  const changeMap = useMemo(() => {
    const map = new Map<string, GitChange>()
    for (const change of changes?.changes ?? []) map.set(change.path, change)
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

  const changeGroups = useMemo(() => {
    const groups: Array<{ label: string; items: GitChange[] }> = [
      { label: 'Staged', items: [] },
      { label: 'Changes', items: [] },
      { label: 'Untracked', items: [] },
    ]
    for (const change of changes?.changes ?? []) {
      const group = change.status === 'untracked' ? 2 : change.staged && !change.unstaged ? 0 : 1
      groups[group].items.push(change)
    }
    return groups.filter((group) => group.items.length)
  }, [changes])

  const join = useCallback((name: string) => (listing?.path ? `${listing.path}/${name}` : name), [listing])
  // Typing stays responsive while a long listing re-filters in the background.
  const needle = useDeferredValue(filter).trim().toLowerCase()
  const visible = useCallback((entries: DirEntry[]) => sortEntries(entries.filter((entry) => showHidden || !entry.hidden), sort), [showHidden, sort])
  const rows = useMemo(() => {
    const out: TreeRow[] = []
    if (!listing) return out
    if (needle) {
      // Filtering: this folder's own entries, flat.
      for (const entry of visible(listing.entries)) {
        if (entry.name.toLowerCase().includes(needle)) out.push({ entry, full: join(entry.name), depth: 0, parent: listing.path })
      }
      return out
    }
    const walk = (dir: string, entries: DirEntry[], depth: number) => {
      for (const entry of visible(entries)) {
        const full = dir ? `${dir}/${entry.name}` : entry.name
        out.push({ entry, full, depth, parent: dir })
        const child = entry.type === 'dir' && open.has(full) ? children[full] : undefined
        if (child) walk(full, child.listing.entries, depth + 1)
      }
    }
    walk(listing.path, listing.entries, 0)
    return out
  }, [listing, needle, visible, join, open, children])
  // Workspace hits, minus the ones already listed for this folder.
  const workspaceHits = useMemo(() => {
    if (!needle || !found || found.session !== sessionId) return []
    // Until the typed query's answer arrives, narrow the previous one locally.
    const results = narrowMatches(found.results, (item) => item.path, found.query, needle) ?? []
    const shown = new Set(rows.map((row) => row.full))
    return results.filter((item) => !shown.has(item.path) && (showHidden || !isHiddenPath(item.path)))
  }, [needle, found, rows, showHidden, sessionId])
  const crumbs = listing?.path ? listing.path.split('/') : []
  const changeCount = changes?.changes.length ?? 0

  const browse = (target: string) => {
    setMode('browse')
    setFilter('')
    setPath(target)
  }
  // Show *target* in the tree: open its folders, then scroll to and focus it.
  const reveal = (target: string) => {
    setMode('browse')
    setFilter('')
    if (root && !target.startsWith(`${root}/`)) setPath('')
    const parts = target.split('/')
    setOpen((current) => new Set([...current, ...parts.slice(1).map((_, at) => parts.slice(0, at + 1).join('/'))]))
    setRevealing(target)
  }
  useEffect(() => {
    if (revealing === null || mode !== 'browse') return
    const row = [...(list.current?.querySelectorAll<HTMLButtonElement>('.file-row') ?? [])].find((item) => item.dataset.path === revealing)
    if (!row) return
    row.scrollIntoView({ block: 'center' })
    row.focus({ preventScroll: true })
    setRevealing(null)
  }, [revealing, rows, mode])

  const openFound = (item: FoundPath) => {
    if (item.type === 'file') onOpen(item.path, changeMap.has(item.path) ? true : undefined)
    else {
      reveal(item.path)
      toggle(item.path, true)
    }
  }
  // Enter with no match in this folder opens the first workspace match for
  // exactly the typed query. Hits narrowed from an earlier query's answer can
  // rank differently, so use this query's own answer, looked up if need be.
  const openFirstHit = async (query: string) => {
    if (!query) return
    const key = `${sessionId}\u0000${query}`
    let answer: FindResult | undefined = found?.session === sessionId && found.query === query ? found : findCache.get(key)
    if (!answer) {
      try {
        answer = await findFiles(sessionId, query)
      } catch {
        return
      }
      findCache.set(key, answer)
    }
    // Typing moved on while the answer was on its way.
    if (typed.current.trim() !== query) return
    const hit = answer.results.find((item) => showHidden || !isHiddenPath(item.path))
    if (hit) openFound(hit)
  }

  const menuFor = (target: string, type: 'dir' | 'file', inTree: boolean) => (event: ReactMouseEvent<HTMLElement>) => {
    const changed = type === 'file' && changeMap.has(target)
    const mention = type === 'dir' ? `${target}/` : target
    const absolute = listing ? `${listing.root.replace(/\/+$/, '')}/${target}` : null
    const items: MenuEntry[] = []
    if (type === 'file') {
      items.push({ label: 'Open', icon: FileCode, onSelect: () => onOpen(target) })
      if (changed) items.push({ label: 'Open changes', icon: FileDiff, onSelect: () => onOpen(target, true) })
      if (changes?.git) items.push({ label: 'File history', icon: History, onSelect: () => onOpen(target, undefined, undefined, 'history') })
    } else items.push({ label: 'Browse into folder', icon: FolderOpen, onSelect: () => browse(target) })
    if (!inTree) items.push({ label: 'Reveal in file tree', icon: ListTree, onSelect: () => reveal(target) })
    items.push('separator', { label: 'Mention in message', icon: AtSign, onSelect: () => onMention(mention) })
    if (onAsk && type === 'file') {
      items.push({ label: 'Ask to explain', icon: BookOpen, onSelect: () => onAsk(`Explain @${mention} `) })
      if (changed) items.push({ label: 'Ask to review changes', icon: MessageSquareCode, onSelect: () => onAsk(`Review the uncommitted changes in @${mention} `) })
      items.push({ label: 'Ask to write tests', icon: FlaskConical, onSelect: () => onAsk(`Write tests for @${mention} `) })
    }
    items.push('separator', { label: 'Copy relative path', icon: Link, onSelect: () => void copyText(target) })
    if (absolute) items.push({ label: 'Copy absolute path', icon: Link, onSelect: () => void copyText(absolute) })
    if (type === 'file') {
      items.push(
        { label: 'Copy contents', icon: Copy, onSelect: () => void copyFileContents(sessionId, target) },
        { label: 'Download', icon: Download, onSelect: () => download(sessionId, target) },
      )
    }
    if (onTerminal) {
      items.push('separator', { label: 'Open in terminal', icon: SquareTerminal, onSelect: () => onTerminal(type === 'dir' ? target : parentOf(target)) })
    }
    openMenu(event, `Actions for ${target}`, items)
  }
  // Stable for the memoized search view; the menu itself reads the latest state.
  const latestMenu = useRef(menuFor)
  latestMenu.current = menuFor
  const searchMenu = useCallback(
    (event: ReactMouseEvent<HTMLElement>, file: string) => latestMenu.current(file, 'file', false)(event),
    [],
  )

  const SortIcon = SORTS[sort].icon
  const branchInfo = changes?.branch ? (
    <div className="list-caption">
      <FolderGit2 size={13} />{changes.branch}
      {changes.ahead || changes.behind ? (
        <span className="tracking" data-tip={`Compared with ${changes.upstream}`}>
          {changes.ahead ? <span>↑{changes.ahead}</span> : null}
          {changes.behind ? <span>↓{changes.behind}</span> : null}
        </span>
      ) : null}
    </div>
  ) : null

  return (
    <div className="files">
      <div className="panel-toolbar files-toolbar">
        <div className="files-tabs" role="tablist" aria-label="Files view">
          <button type="button" role="tab" aria-selected={mode === 'browse'} aria-label="Files" className={mode === 'browse' ? 'on' : ''} onClick={() => setMode('browse')}>
            <FolderTree size={14} /><span className="tab-label">Files</span>
          </button>
          {changes?.git ? (
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'changes'}
              aria-label={`Changes${changeCount ? ` (${changeCount})` : ''}`}
              className={mode === 'changes' ? 'on' : ''}
              data-tip={`Changed files${changes.branch ? ` on ${changes.branch}` : ''}`}
              onClick={() => setMode('changes')}
            >
              <GitBranch size={14} /><span className="tab-label">Changes</span>
              {changeCount ? <span className="tab-count">{changeCount}</span> : null}
            </button>
          ) : null}
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'search'}
            aria-label="Search"
            className={mode === 'search' ? 'on' : ''}
            data-tip={`Search in files (${MOD_KEY}⇧F)`}
            onClick={() => {
              setMode('search')
              setSearchFocus((value) => value + 1)
            }}
          >
            <TextSearchIcon size={14} /><span className="tab-label">Search</span>
          </button>
        </div>
        <span className="grow" />
        {mode === 'browse' ? (
          <IconButton
            icon={SortIcon}
            size="sm"
            label={SORTS[sort].label}
            onClick={() => {
              const next = SORTS[sort].next
              store.set('ene.files.sort', next === 'name' ? null : next)
              setSort(next)
            }}
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
      {mode === 'browse' && crumbs.length ? (
        <div className="crumbs" role="navigation" aria-label="Folder path">
          <button type="button" onClick={() => browse('')} data-tip={listing?.root ?? 'Workspace'} aria-label="Workspace root">
            <House size={14} />
          </button>
          {crumbs.map((crumb, index) => (
            <span className="crumb" key={`${index}-${crumb}`}>
              <ChevronRight size={12} />
              <button type="button" onClick={() => browse(crumbs.slice(0, index + 1).join('/'))}>{crumb}</button>
            </span>
          ))}
        </div>
      ) : null}
      {mode === 'browse' ? (
        <label className="filter">
          <Search size={14} />
          <input
            {...NO_AUTOFILL}
            ref={filterInput}
            value={filter}
            placeholder={`Find files (${MOD_KEY}P)`}
            aria-label="Filter this folder and find files in the workspace"
            onChange={(event) => setFilter(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape' && filter) {
                event.preventDefault()
                setFilter('')
              } else if (event.key === 'ArrowDown') {
                event.preventDefault()
                list.current?.querySelector<HTMLButtonElement>('.file-row')?.focus()
              } else if (event.key === 'Enter') {
                const row = rows[0]
                if (row) openFound({ path: row.full, type: row.entry.type })
                else void openFirstHit(filter.trim())
              }
            }}
          />
          {finding ? <Spinner /> : null}
          {filter ? <button type="button" aria-label="Clear filter" onClick={() => setFilter('')}><X size={13} /></button> : null}
        </label>
      ) : null}
      {error ? <div className="panel-error">{error}</div> : null}
      {mode === 'search' ? (
        <TextSearch
          sessionId={sessionId}
          refreshKey={refreshKey}
          focusSignal={searchFocus}
          onOpen={(file, line) => onOpen(file, undefined, line)}
          onMention={onMention}
          onMenu={searchMenu}
        />
      ) : mode === 'changes' ? (
        <div className="file-list" ref={list} onKeyDown={(event) => onTreeKey(event, toggle)}>
          {branchInfo}
          {changes?.error ? <div className="panel-error">{changes.error}</div> : null}
          {changeCount === 0 && !changes?.error ? <Empty icon={GitBranch}>Working tree clean.</Empty> : null}
          {changeGroups.map((group) => (
            <div key={group.label} role="group" aria-label={group.label}>
              <div className="list-caption">{group.label}<span className="count">{group.items.length}</span></div>
              {group.items.map((change) => {
                const name = baseName(change.path)
                const Icon = fileIcon(name, 'file')
                return (
                  <FileItem
                    key={change.path}
                    path={change.path}
                    onMention={onMention}
                    onMenu={menuFor(change.path, 'file', false)}
                    onClick={() => onOpen(change.path, true)}
                    title={change.staged && change.unstaged ? 'Partially staged' : undefined}
                    attrs={{ 'data-path': change.path, 'data-kind': 'file' }}
                  >
                    <Icon size={15} className="file-icon" />
                    <span className="file-name">
                      {name}
                      <span className="file-dir">{change.path.slice(0, -name.length)}</span>
                    </span>
                    <LineStat added={change.added} removed={change.removed} />
                    <span className={`git-badge ${change.status}`} data-tip={change.staged && change.unstaged ? `${change.status}, partially staged` : change.status}>
                      {STATUS_LETTER[change.status]}
                    </span>
                  </FileItem>
                )
              })}
            </div>
          ))}
        </div>
      ) : (
        <div className="file-list" ref={list} onKeyDown={(event) => onTreeKey(event, toggle)}>
          {listing?.path && !needle ? (
            <button type="button" className="file-row up" onClick={() => browse(crumbs.slice(0, -1).join('/'))}>
              <Folder size={15} className="file-icon" />
              <span className="file-name">..</span>
            </button>
          ) : null}
          {needle && rows.length ? <div className="list-caption">{listing?.path ? 'This folder' : 'Workspace root'}</div> : null}
          {rows.map(({ entry, full, depth, parent }) => {
            const Icon = fileIcon(entry.name, entry.type)
            const isDir = entry.type === 'dir'
            const status = isDir ? undefined : changeMap.get(full)?.status
            const dirty = isDir && dirtyDirs.has(full)
            const expanded = isDir && !needle ? open.has(full) : undefined
            return (
              <FileItem
                key={full}
                path={isDir ? `${full}/` : full}
                onMention={onMention}
                onMenu={menuFor(full, entry.type, true)}
                className={entry.hidden ? 'hidden-entry' : ''}
                depth={depth}
                expanded={expanded}
                onClick={() => (isDir && !needle ? toggle(full) : openFound({ path: full, type: entry.type }))}
                title={relativeTime(entry.mtime)}
                attrs={{ 'data-path': full, 'data-kind': entry.type, 'data-parent': parent }}
              >
                {isDir && !needle
                  ? <ChevronRight size={13} className={`tree-chevron ${expanded ? 'open' : ''}`} />
                  : <span className="tree-chevron" />}
                <Icon size={15} className={`file-icon ${entry.type}`} />
                <span className="file-name">{entry.name}{entry.symlink ? <span className="file-dir"> ↪</span> : null}</span>
                {status ? <span className={`git-badge ${status}`} data-tip={status}>{STATUS_LETTER[status]}</span> : null}
                {dirty ? <span className="dirty-dot" data-tip="Contains changes" /> : null}
                {isDir ? null : <span className="file-size">{bytes(entry.size)}</span>}
              </FileItem>
            )
          })}
          {needle && workspaceHits.length ? <div className="list-caption">Workspace</div> : null}
          {needle && workspaceHits.map((item) => {
            const name = baseName(item.path)
            const Icon = fileIcon(name, item.type)
            const status = item.type === 'file' ? changeMap.get(item.path)?.status : undefined
            const ranges = pathMatchRanges(item.path, needle)
            const nameStart = item.path.length - name.length
            return (
              <FileItem
                key={item.path}
                path={item.type === 'dir' ? `${item.path}/` : item.path}
                onMention={onMention}
                onMenu={menuFor(item.path, item.type, false)}
                onClick={() => openFound(item)}
                attrs={{ 'data-path': item.path, 'data-kind': 'file' }}
              >
                <Icon size={15} className={`file-icon ${item.type}`} />
                <span className="file-name">
                  <Highlighted text={name} ranges={shiftRanges(ranges, -nameStart, name.length)} />
                  <span className="file-dir">
                    <Highlighted text={item.path.slice(0, nameStart)} ranges={shiftRanges(ranges, 0, nameStart)} />
                  </span>
                </span>
                {status ? <span className={`git-badge ${status}`} data-tip={status}>{STATUS_LETTER[status]}</span> : null}
              </FileItem>
            )
          })}
          {listing && rows.length === 0 && !(needle && (workspaceHits.length || finding || findQuery !== filter.trim()))
            ? <Empty icon={Folder}>{needle ? 'No matches.' : 'Empty folder.'}</Empty>
            : null}
          {needle && found?.truncated ? <div className="list-caption">Showing the best {found.results.length} workspace matches.</div> : null}
          {listing?.truncated ? <div className="list-caption">Showing the first 2000 entries.</div> : null}
          {!listing && loading ? <div className="center-pad"><Spinner /></div> : null}
        </div>
      )}
      {menu ? <ContextMenu menu={menu} onClose={closeMenu} /> : null}
    </div>
  )
})

function LineStat({ added, removed }: { added: number | null; removed: number | null }) {
  if (added === null && removed === null) return null
  return (
    <span className="line-stat">
      {added ? <b className="ok">+{added}</b> : null}
      {removed ? <b className="fail">−{removed}</b> : null}
    </span>
  )
}

/** Ranges moved by `delta` and clipped to `[0, length)`. */
function shiftRanges(ranges: Range[], delta: number, length: number): Range[] {
  return ranges
    .map(([start, end]): Range => [Math.max(0, start + delta), Math.min(length, end + delta)])
    .filter(([start, end]) => end > start)
}

type CodeRow = { html: string | null; text: string }

/**
 * One lazily painted block of the code view. Props outside the block are
 * normalized (null, '') so unrelated selection or find changes skip it.
 */
const CodeChunk = memo(function CodeChunk({
  rows,
  start,
  target,
  selected,
  matched,
  current,
}: {
  rows: CodeRow[]
  start: number
  target: number | null
  /** `first-last` (1-based) clipped to this block, or ''. */
  selected: string
  /** Comma-separated 0-based lines with find matches in this block. */
  matched: string
  current: number | null
}) {
  const [first, last] = selected ? selected.split('-').map(Number) : [0, -1]
  const matches = useMemo(() => new Set(matched ? matched.split(',').map(Number) : []), [matched])
  return (
    <div className="code-chunk">
      {rows.map((row, offset) => {
        const no = start + offset + 1
        const classes = [
          'code-line',
          no === target ? 'target' : '',
          no >= first && no <= last ? 'selected' : '',
          matches.has(no - 1) ? 'match' : '',
          current === no - 1 ? 'current' : '',
        ].filter(Boolean).join(' ')
        return (
          <div className={classes} key={offset} data-line={no}>
            <span className="ln" aria-hidden>{no}</span>
            {row.html !== null
              ? <code className="hljs" dangerouslySetInnerHTML={{ __html: row.html || ' ' }} />
              : <code>{row.text || ' '}</code>}
          </div>
        )
      })}
    </div>
  )
})

function DiffBody({ path, diff, error, rows }: { path: string; diff: GitDiff | null; error: string; rows: SplitLine[] | null }) {
  if (diff === null) return error ? <div className="panel-error">{error}</div> : <div className="center-pad"><Spinner /></div>
  if (diff.binary) return <Empty icon={File}>Binary file changed.</Empty>
  if (diff.too_large) return <Empty icon={File}>Too large to diff.</Empty>
  return <SplitDiff path={path} oldText={diff.old} newText={diff.new} lines={rows ?? undefined} />
}

const diffStat = (rows: SplitLine[] | null) => rows && {
  added: rows.filter((line) => line.right?.kind === 'add').length,
  removed: rows.filter((line) => line.left?.kind === 'del').length,
}

const diffRowsOf = (diff: GitDiff | null) => (diff && !diff.binary && !diff.too_large ? splitLines(diff.old, diff.new) : null)

const CHANGE_ROW = '.s-row.chg, .u-row.add, .u-row.del'

/** Scroll *body* to the next (1) or previous (-1) block of changed diff rows. */
function jumpToChange(body: HTMLElement | null, direction: 1 | -1) {
  if (!body) return
  const starts = [...body.querySelectorAll<HTMLElement>(CHANGE_ROW)].filter(
    (row) => !row.previousElementSibling?.matches(CHANGE_ROW),
  )
  const top = body.getBoundingClientRect().top
  const offsets = starts.map((row) => row.getBoundingClientRect().top - top)
  // A little slack so the block already at the top counts as "here".
  let index = -1
  if (direction > 0) index = offsets.findIndex((offset) => offset > 40)
  else for (let at = offsets.length - 1; at >= 0 && index < 0; at--) if (offsets[at] < 30) index = at
  if (index < 0) return
  body.scrollTop += offsets[index] - 32
}

export const FileViewer = memo(function FileViewer({
  sessionId,
  path,
  changed,
  line,
  view,
  onClose,
  onMention,
}: {
  sessionId: string
  path: string
  /** True: open on the diff; undefined: probe Git and offer the diff if changed. */
  changed?: boolean
  /** 1-based line to scroll to and mark in the file view (from a search hit). */
  line?: number
  /** Open on this tab instead of the file or its diff. */
  view?: FileView
  onClose: () => void
  onMention: (path: string) => void
}) {
  const [file, setFile] = useState<FilePreview | null>(null)
  const [error, setError] = useState('')
  const [diff, setDiff] = useState<GitDiff | null>(null)
  const [diffError, setDiffError] = useState('')
  const [mode, setMode] = useState<'diff' | 'file' | 'history'>(view ?? (changed ? 'diff' : 'file'))
  // A line target needs the source view, so Markdown is not rendered then.
  const [rendered, setRendered] = useState(/\.md$/i.test(path) && !line)
  const [tick, setTick] = useState(0)
  const [target, setTarget] = useState<number | null>(line ?? null)
  const [wrap, setWrap] = useState(() => store.get('ene.viewer.nowrap') !== '1')
  // Selected lines (1-based), from the gutter or a text selection.
  const [selection, setSelection] = useState<{ anchor: number; head: number } | null>(null)
  const [finding, setFinding] = useState(false)
  const [query, setQuery] = useState('')
  const [current, setCurrent] = useState(0)
  const [log, setLog] = useState<GitLog | null>(null)
  const [logError, setLogError] = useState('')
  const [commit, setCommit] = useState<GitCommit | null>(null)
  const [commitDiff, setCommitDiff] = useState<GitDiff | null>(null)
  const [commitError, setCommitError] = useState('')
  const codeView = useRef<HTMLDivElement>(null)
  const markdownView = useRef<HTMLDivElement>(null)
  const body = useRef<HTMLDivElement>(null)
  const findInput = useRef<HTMLInputElement>(null)
  const { menu, open: openMenu, close: closeMenu } = useContextMenu()

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
        if (data.status === 'deleted') setMode((value) => (value === 'history' ? value : 'diff'))
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

  // History loads when its tab first opens, and again on reload.
  useEffect(() => {
    if (mode !== 'history') return
    let cancelled = false
    setLogError('')
    fileLog(sessionId, path)
      .then((data) => !cancelled && setLog(data))
      .catch((exc) => {
        if (cancelled) return
        // FastAPI's own 404 for an unknown route: the hub predates this page.
        const stale = exc instanceof ApiError && exc.status === 404 && exc.message === 'Not Found'
        setLogError(stale ? 'This hub is older than the page and has no file history. Restart `ene hub`.' : errorMessage(exc, 'Could not read the history.'))
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, path, mode, tick])

  useEffect(() => {
    setCommitDiff(null)
    setCommitError('')
    if (!commit?.path) return
    let cancelled = false
    fileDiff(sessionId, commit.path, commit.hash)
      .then((data) => !cancelled && setCommitDiff(data))
      .catch((exc) => !cancelled && setCommitError(errorMessage(exc, 'Could not load this commit.')))
    return () => {
      cancelled = true
    }
  }, [sessionId, commit])

  const language = languageForPath(path)
  const text = file?.kind === 'text' ? file.text ?? '' : ''
  // One row per line so long lines wrap without losing their line number.
  const lines = useMemo(() => {
    if (!text) return [] as CodeRow[]
    const plain = text.split('\n')
    const html = highlight(text, language)
    const marked = html !== null ? splitHighlightedLines(html) : null
    return plain.map((value, index) => ({ html: marked?.[index] ?? null, text: value }))
  }, [text, language])
  const lineCount = lines.length
  const isMarkdown = /\.(md|markdown)$/i.test(path)
  const Icon = fileIcon(path, 'file')
  const hasDiff = diff !== null && diff.status !== 'unchanged'
  // The file is in a repo exactly when its diff against HEAD loads.
  const inGit = diff !== null
  // Listed as changed opens on the diff while it loads; a file Git lists but
  // whose content matches HEAD (a mode change, say) then shows the file.
  const showDiff = mode === 'diff' && (diff ? hasDiff : changed)
  const showHistory = mode === 'history'
  // Diffed once: the stat and the split view share the same rows.
  const diffRows = useMemo(() => diffRowsOf(diff), [diff])
  const stat = useMemo(() => diffStat(diffRows), [diffRows])
  const commitRows = useMemo(() => diffRowsOf(commitDiff), [commitDiff])
  const commitStat = useMemo(() => diffStat(commitRows), [commitRows])
  const chunks = useMemo(() => {
    const result: Array<{ start: number; lines: CodeRow[] }> = []
    for (let start = 0; start < lines.length; start += CODE_CHUNK) {
      result.push({ start, lines: lines.slice(start, start + CODE_CHUNK) })
    }
    return result
  }, [lines])

  // Find works on the source and on rendered Markdown alike.
  const findable = !showDiff && !showHistory && file?.kind === 'text'
  const markdownShown = findable && rendered && isMarkdown
  const codeShown = findable && !markdownShown
  const diffShown = showDiff || (showHistory && commit !== null)
  const relative = !/^[/~]/.test(path)

  // Find in file; `:123` jumps to a line instead.
  const deferredQuery = useDeferredValue(query)
  const goto = gotoLine(deferredQuery)
  const found = useMemo(
    () => (finding && goto === null ? findInLines(lines.map((row) => row.text), deferredQuery) : { matches: [] as LineMatch[], truncated: false }),
    [finding, goto, lines, deferredQuery],
  )
  // Rendered Markdown is searched in the page itself, after it renders.
  const [domFound, setDomFound] = useState<ReturnType<typeof findInElement>>({ ranges: [], truncated: false })
  useEffect(() => {
    const view = markdownView.current
    setDomFound(finding && markdownShown && goto === null && view ? findInElement(view, deferredQuery) : { ranges: [], truncated: false })
  }, [finding, markdownShown, goto, deferredQuery, text])
  const matchCount = codeShown ? found.matches.length : domFound.ranges.length
  const truncated = codeShown ? found.truncated : domFound.truncated
  const index = matchCount ? Math.min(current, matchCount - 1) : 0
  const currentMatch = codeShown && matchCount ? found.matches[index] : null
  const currentRange = markdownShown && matchCount ? domFound.ranges[index] : null
  useEffect(() => setCurrent(0), [deferredQuery, markdownShown])
  const matchedByChunk = useMemo(() => {
    const byChunk = new Map<number, number[]>()
    for (const match of found.matches) {
      const chunk = Math.floor(match.line / CODE_CHUNK)
      const list = byChunk.get(chunk) ?? []
      if (list.at(-1) !== match.line) list.push(match.line)
      byChunk.set(chunk, list)
    }
    return new Map([...byChunk].map(([chunk, list]) => [chunk, list.join(',')]))
  }, [found])

  useEffect(() => {
    if (goto === null || !lineCount) return
    // Lines are numbered in the source, so a jump leaves rendered Markdown.
    setRendered(false)
    setTarget(Math.min(goto, lineCount))
  }, [goto, lineCount])

  useEffect(() => {
    if (!target || !codeShown) return
    codeView.current?.querySelector('.code-line.target')?.scrollIntoView({ block: 'center' })
  }, [target, codeShown, lines])

  // Paint matches as ranges (no DOM changes) where the browser supports it;
  // matching lines are tinted either way.
  useEffect(() => {
    const view = codeView.current
    if (!canHighlight()) return
    CSS.highlights.delete('ene-find')
    CSS.highlights.delete('ene-find-current')
    if (markdownShown && matchCount) {
      CSS.highlights.set('ene-find', new Highlight(...domFound.ranges.slice(0, 2000)))
      if (currentRange) CSS.highlights.set('ene-find-current', new Highlight(currentRange))
      return
    }
    if (!view || !codeShown || !matchCount) return
    const lineCode = (index: number) => view.querySelector(`.code-line[data-line="${index + 1}"] > code`)
    const all = new Highlight()
    for (const match of found.matches.slice(0, 2000)) {
      const element = lineCode(match.line)
      const range = element && textRange(element, match.start, match.end)
      if (range) all.add(range)
    }
    CSS.highlights.set('ene-find', all)
    if (currentMatch) {
      const element = lineCode(currentMatch.line)
      const range = element && textRange(element, currentMatch.start, currentMatch.end)
      if (range) CSS.highlights.set('ene-find-current', new Highlight(range))
    }
  }, [found, currentMatch, codeShown, matchCount, chunks, markdownShown, domFound, currentRange])
  useEffect(() => () => {
    if (!canHighlight()) return
    CSS.highlights.delete('ene-find')
    CSS.highlights.delete('ene-find-current')
  }, [])

  useEffect(() => {
    if (currentMatch && codeShown) {
      codeView.current?.querySelector(`.code-line[data-line="${currentMatch.line + 1}"]`)?.scrollIntoView({ block: 'center' })
    } else if (currentRange) {
      const element = currentRange.startContainer.parentElement
      element?.scrollIntoView({ block: 'center' })
    }
  }, [currentMatch, codeShown, currentRange])

  const openFind = useCallback((initial?: string) => {
    if (file?.kind !== 'text') return
    setMode('file')
    setFinding(true)
    if (initial !== undefined) setQuery(initial)
    window.setTimeout(() => {
      findInput.current?.focus()
      if (initial === undefined) findInput.current?.select()
    }, 0)
  }, [file])

  // Captured before the app's own shortcuts, which would open the
  // conversation search behind this dialog.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = hasShortcutModifier(event)
      const key = event.key.toLowerCase()
      if (mod && !event.shiftKey && !event.altKey && (key === 'f' || key === 'g')) {
        event.preventDefault()
        openFind(key === 'g' ? ':' : undefined)
      } else if (event.altKey && !mod && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault()
        jumpToChange(body.current, event.key === 'ArrowDown' ? 1 : -1)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [openFind])

  const step = (direction: 1 | -1) => {
    if (matchCount) setCurrent((value) => (value + direction + matchCount) % matchCount)
  }

  // Gutter clicks select lines (Shift extends); selecting text selects its lines.
  const lineOf = (node: Node | null) => {
    const element = node instanceof Element ? node : node?.parentElement
    const row = element?.closest<HTMLElement>('.code-line')
    return row ? Number(row.dataset.line) : null
  }
  const onGutter = (event: ReactMouseEvent) => {
    const gutter = (event.target as Element).closest('.ln')
    const no = gutter ? lineOf(gutter) : null
    if (no === null) return
    event.preventDefault()
    setSelection((value) => (event.shiftKey && value ? { anchor: value.anchor, head: no } : { anchor: no, head: no }))
  }
  const onTextSelect = () => {
    const picked = window.getSelection()
    if (!picked || picked.isCollapsed || !codeView.current?.contains(picked.anchorNode)) return
    const anchor = lineOf(picked.anchorNode)
    const head = lineOf(picked.focusNode)
    if (anchor !== null && head !== null) setSelection({ anchor, head })
  }
  const [first, last] = selection ? lineSpan(selection) : [0, 0]

  const head = showHistory
    ? commit
      ? <>{commit.short} · {commit.author}{commitStat ? <> · <b className="ok">+{commitStat.added}</b> <b className="fail">−{commitStat.removed}</b></> : null}</>
      : `History${log ? ` · ${log.commits.length}${log.truncated ? '+' : ''} commits` : ''}`
    : showDiff && diff
      ? <>{diff.status} vs HEAD{stat ? <> · <b className="ok">+{stat.added}</b> <b className="fail">−{stat.removed}</b></> : null}</>
      : file ? `${bytes(file.size)} · ${relativeTime(file.mtime)}${file.kind === 'text' ? ` · ${lineCount} lines` : ''}` : ''

  // One tab per way to look at the file; Markdown has both a preview and its source.
  const onFile = !showDiff && !showHistory
  const tabs: Array<{ label: string; icon: typeof File; selected: boolean; disabled?: boolean; onSelect: () => void }> = []
  if (hasDiff) tabs.push({ label: 'Changes', icon: FileDiff, selected: Boolean(showDiff), onSelect: () => setMode('diff') })
  const deleted = diff?.status === 'deleted'
  if (isMarkdown && file?.kind !== 'image' && file?.kind !== 'binary') {
    tabs.push(
      { label: 'Preview', icon: FileText, selected: onFile && rendered, disabled: deleted, onSelect: () => { setMode('file'); setRendered(true) } },
      { label: 'Source', icon: FileCode, selected: onFile && !rendered, disabled: deleted, onSelect: () => { setMode('file'); setRendered(false) } },
    )
  } else tabs.push({ label: 'File', icon: Icon, selected: onFile, disabled: deleted, onSelect: () => setMode('file') })
  if (inGit) tabs.push({ label: 'History', icon: History, selected: showHistory, onSelect: () => setMode('history') })

  const moreItems: MenuEntry[] = [{ label: 'Copy path', icon: Link, onSelect: () => void copyText(path) }]
  if (file?.kind === 'text') moreItems.push({ label: 'Copy contents', icon: Copy, onSelect: () => void copyText(text) })
  if (file && relative) moreItems.push({ label: 'Download', icon: Download, onSelect: () => download(sessionId, path) })
  moreItems.push('separator', { label: 'Reload', icon: RefreshCw, onSelect: () => setTick((value) => value + 1) })

  return (
    <Modal onClose={onClose} className="viewer" label={`File ${path}`}>
      <div className="viewer-head">
        <Icon size={16} />
        <div className="viewer-title">
          <span className="mono clamp">{path}</span>
          <span className="muted small">{head}</span>
        </div>
        <span className="grow" />
        <IconButton icon={AtSign} size="sm" label="Mention in message" onClick={() => onMention(path)} />
        <IconButton icon={Ellipsis} size="sm" label="More actions" onClick={(event) => openMenu(event, 'File actions', moreItems)} />
        <IconButton icon={X} size="sm" label="Close (Esc)" onClick={onClose} />
      </div>
      <div className="viewer-bar">
        <div className="view-tabs" role="tablist" aria-label="View">
          {tabs.map((tab) => (
            <button
              type="button"
              role="tab"
              key={tab.label}
              aria-selected={tab.selected}
              aria-label={tab.label}
              className={tab.selected ? 'on' : ''}
              disabled={tab.disabled}
              onClick={tab.onSelect}
            >
              <tab.icon size={14} />
              <span>{tab.label}</span>
            </button>
          ))}
        </div>
        <span className="grow" />
        {diffShown ? (
          <>
            <IconButton icon={ChevronUp} size="sm" label="Previous change (Alt+↑)" onClick={() => jumpToChange(body.current, -1)} />
            <IconButton icon={ChevronDown} size="sm" label="Next change (Alt+↓)" onClick={() => jumpToChange(body.current, 1)} />
          </>
        ) : null}
        {findable ? (
          <IconButton icon={Search} size="sm" label={`Find in file (${MOD_KEY}F)`} active={finding} onClick={() => (finding ? setFinding(false) : openFind())} />
        ) : null}
        {codeShown ? (
          <IconButton
            icon={WrapText}
            size="sm"
            label={wrap ? 'Wrap long lines: on' : 'Wrap long lines: off'}
            active={wrap}
            onClick={() => {
              store.set('ene.viewer.nowrap', wrap ? '1' : null)
              setWrap(!wrap)
            }}
          />
        ) : null}
      </div>
      {finding && findable ? (
        <div className="viewer-find">
          <Search size={14} />
          <input
            {...NO_AUTOFILL}
            ref={findInput}
            value={query}
            placeholder="Find, or :line"
            aria-label="Find in file"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault()
                setFinding(false)
              } else if (event.key === 'Enter') {
                event.preventDefault()
                step(event.shiftKey ? -1 : 1)
              }
            }}
          />
          <span className="muted small find-count">
            {goto !== null ? `Line ${Math.min(goto, lineCount)}` : query ? `${matchCount ? index + 1 : 0}/${matchCount}${truncated ? '+' : ''}` : ''}
          </span>
          <IconButton icon={ChevronUp} size="sm" label="Previous match (⇧Enter)" disabled={!matchCount} onClick={() => step(-1)} />
          <IconButton icon={ChevronDown} size="sm" label="Next match (Enter)" disabled={!matchCount} onClick={() => step(1)} />
          <IconButton icon={X} size="sm" label="Close find (Esc)" onClick={() => setFinding(false)} />
        </div>
      ) : null}
      <div className="viewer-body" ref={body}>
        {showHistory ? (
          commit ? (
            <>
              <div className="commit-head">
                <IconButton icon={ArrowLeft} size="sm" label="Back to history" onClick={() => setCommit(null)} />
                <span className="clamp">{commit.subject}</span>
                <span className="grow" />
                <span className="mono muted small">{relativeTime(commit.time)}</span>
                <CopyButton text={commit.hash} label="Copy commit hash" />
              </div>
              <DiffBody path={commit.path ?? path} diff={commitDiff} error={commitError} rows={commitRows} />
            </>
          ) : logError ? <div className="panel-error">{logError}</div> : !log ? <div className="center-pad"><Spinner /></div> : log.commits.length === 0 ? (
            <Empty icon={History}>No commits yet.</Empty>
          ) : (
            <div className="commit-list">
              {log.commits.map((item) => (
                <button
                  type="button"
                  className="commit-row"
                  key={item.hash}
                  disabled={!item.path}
                  title={item.path ? undefined : 'The file was outside the workspace then.'}
                  onClick={() => setCommit(item)}
                >
                  <span className="clamp">{item.subject}</span>
                  <span className="muted small">
                    <span className="mono">{item.short}</span> · {item.author} · {relativeTime(item.time)}
                    {item.path && item.path !== path ? <> · <span className="mono">{item.path}</span></> : null}
                  </span>
                </button>
              ))}
              {log.truncated ? <div className="list-caption">Showing the latest {log.commits.length} commits.</div> : null}
            </div>
          )
        ) : showDiff ? (
          <DiffBody path={path} diff={diff} error={diffError} rows={diffRows} />
        ) : (
          <>
            {error ? <div className="panel-error">{error}</div> : null}
            {!file && !error ? <div className="center-pad"><Spinner /></div> : null}
            {file?.kind === 'image' ? (
              <div className="viewer-image"><img src={rawFileUrl(sessionId, path)} alt={path} /></div>
            ) : null}
            {file?.kind === 'binary' ? <Empty icon={File}>Binary file ({file.mime}) — no preview.</Empty> : null}
            {file?.kind === 'text' && rendered && isMarkdown ? (
              <div className="viewer-markdown" ref={markdownView}><Markdown text={text} /></div>
            ) : null}
            {file?.kind === 'text' && !(rendered && isMarkdown) ? (
              <div className={`code-view ${wrap ? '' : 'nowrap'}`} ref={codeView} onMouseDown={onGutter} onMouseUp={onTextSelect}>
                {/* Chunks off screen skip layout and paint (content-visibility). */}
                {chunks.map((chunk, index) => {
                  const end = chunk.start + chunk.lines.length
                  const inChunk = (no: number | null) => (no !== null && no > chunk.start && no <= end ? no : null)
                  return (
                    <CodeChunk
                      key={chunk.start}
                      rows={chunk.lines}
                      start={chunk.start}
                      target={inChunk(target)}
                      selected={selection && first <= end && last > chunk.start ? `${Math.max(first, chunk.start + 1)}-${Math.min(last, end)}` : ''}
                      matched={matchedByChunk.get(index) ?? ''}
                      current={currentMatch && inChunk(currentMatch.line + 1) ? currentMatch.line : null}
                    />
                  )
                })}
              </div>
            ) : null}
            {file?.truncated ? <div className="list-caption">Preview truncated at 512 KB.</div> : null}
          </>
        )}
      </div>
      {selection && codeShown ? (
        <div className="selection-bar">
          <span className="small">{last > first ? `Lines ${first}–${last}` : `Line ${first}`}</span>
          <span className="grow" />
          <button type="button" className="btn" onClick={() => onMention(lineRef(path, first, last))}>
            <AtSign size={13} /> Mention
          </button>
          <CopyButton text={() => lines.slice(first - 1, last).map((row) => row.text).join('\n')} label="Copy lines" />
          <IconButton icon={X} size="sm" label="Clear selection" onClick={() => setSelection(null)} />
        </div>
      ) : null}
      {menu ? <ContextMenu menu={menu} onClose={closeMenu} /> : null}
    </Modal>
  )
})
