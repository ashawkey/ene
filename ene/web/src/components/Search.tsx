import { CaseSensitive, ChevronDown, ChevronRight, Ellipsis, Regex, Search, WholeWord, X } from 'lucide-react'
import { memo, useEffect, useMemo, useRef, useState } from 'react'

import { errorMessage, searchText } from '../lib/api'
import { NO_AUTOFILL } from '../lib/noAutofill'
import type { SearchOptions, SearchResult } from '../lib/protocol'
import { type Range, splitRanges, useDebounced, utf16Ranges } from '../lib/search'
import { store } from '../lib/storage'
import { FileItem, fileIcon } from './Files'
import { Empty, IconButton, Spinner } from './ui'

const DEFAULT_OPTIONS: SearchOptions = { regex: false, case: false, word: false, include: '', exclude: '' }

function loadState(sessionId: string): { query: string; options: SearchOptions } {
  try {
    const saved = JSON.parse(store.get(`ene.search.${sessionId}`) ?? 'null')
    if (saved && typeof saved.query === 'string') {
      // Stored flat, as the request below: { query, regex, case, ... }.
      const { query, ...options } = saved
      return { query, options: { ...DEFAULT_OPTIONS, ...options } }
    }
  } catch {
    // A corrupt entry just resets the search.
  }
  return { query: '', options: DEFAULT_OPTIONS }
}

export function Highlighted({ text, ranges }: { text: string; ranges: Range[] }) {
  return (
    <>
      {splitRanges(text, ranges).map((part, index) => (part.hit ? <mark key={index}>{part.text}</mark> : part.text))}
    </>
  )
}

const TRUNCATED: Record<string, string> = {
  limit: 'Showing the first results only. Refine the search to see more.',
  timeout: 'Search stopped after 5 s. Results are partial.',
}

/** Full-text search across the session workspace (read-only, via ripgrep on the hub). */
export const TextSearch = memo(function TextSearch({
  sessionId,
  onOpen,
  onMention,
  refreshKey,
  focusSignal,
}: {
  sessionId: string
  onOpen: (path: string, line: number) => void
  onMention: (path: string) => void
  refreshKey: number
  focusSignal: number
}) {
  const initial = useMemo(() => loadState(sessionId), [sessionId])
  const [query, setQuery] = useState(initial.query)
  const [options, setOptions] = useState<SearchOptions>(initial.options)
  const [showGlobs, setShowGlobs] = useState(Boolean(initial.options.include || initial.options.exclude))
  const [result, setResult] = useState<SearchResult | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set())
  const [tick, setTick] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [focusSignal])

  // One key for the whole request so a toggle and typing debounce together.
  const request = useDebounced(JSON.stringify({ query, ...options }), 300)

  useEffect(() => {
    store.set(`ene.search.${sessionId}`, request)
    const { query: text, ...rest } = JSON.parse(request) as SearchOptions & { query: string }
    if (!text) {
      setResult(null)
      setError('')
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    searchText(sessionId, text, rest)
      .then((data) => {
        if (cancelled) return
        setResult(data)
        setError('')
        setCollapsed(new Set())
      })
      .catch((exc) => {
        if (cancelled) return
        setResult(null)
        setError(errorMessage(exc, 'Search failed.'))
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [sessionId, request, tick, refreshKey])

  const capped = useMemo(() => result?.files.some((file) => file.capped) ?? false, [result])

  const toggle = (key: 'regex' | 'case' | 'word') => setOptions((value) => ({ ...value, [key]: !value[key] }))
  const fold = (path: string) => setCollapsed((value) => {
    const next = new Set(value)
    if (next.has(path)) next.delete(path)
    else next.add(path)
    return next
  })

  return (
    <div className="text-search">
      <div className="filter search-box">
        <Search size={14} />
        <input
          {...NO_AUTOFILL}
          ref={input}
          value={query}
          placeholder="Search in files"
          aria-label="Search in files"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') setTick((value) => value + 1)
          }}
        />
        {query ? <button type="button" aria-label="Clear search" onClick={() => setQuery('')}><X size={13} /></button> : null}
        <IconButton icon={CaseSensitive} size="sm" label="Match case" active={options.case} onClick={() => toggle('case')} />
        <IconButton icon={WholeWord} size="sm" label="Match whole word" active={options.word} onClick={() => toggle('word')} />
        <IconButton icon={Regex} size="sm" label="Use regular expression" active={options.regex} onClick={() => toggle('regex')} />
        <IconButton icon={Ellipsis} size="sm" label="Files to include or exclude" active={showGlobs} onClick={() => setShowGlobs(!showGlobs)} />
      </div>
      {showGlobs ? (
        <div className="search-globs">
          <input
            {...NO_AUTOFILL}
            value={options.include}
            placeholder="Files to include (e.g. *.py, src/**)"
            aria-label="Files to include"
            onChange={(event) => setOptions({ ...options, include: event.target.value })}
          />
          <input
            {...NO_AUTOFILL}
            value={options.exclude}
            placeholder="Files to exclude (e.g. tests, *.min.js)"
            aria-label="Files to exclude"
            onChange={(event) => setOptions({ ...options, exclude: event.target.value })}
          />
        </div>
      ) : null}
      {error ? <div className="panel-error">{error}</div> : null}
      {result && query ? (
        <div className="list-caption search-summary">
          {result.match_count
            ? `${result.match_count}${result.truncated || capped ? '+' : ''} results in ${result.file_count} files`
            : 'No results'}
          {loading ? <Spinner /> : null}
        </div>
      ) : loading ? <div className="center-pad"><Spinner /></div> : null}
      {result?.reason && TRUNCATED[result.reason] ? <div className="search-note">{TRUNCATED[result.reason]}</div> : null}
      {!query && !error ? <Empty icon={Search}>Search file contents across the workspace. Ignored files are skipped.</Empty> : null}
      <div className="file-list search-results">
        {query && result?.files.map((file) => {
          const name = file.path.split('/').at(-1) ?? file.path
          const Icon = fileIcon(name, 'file')
          const open = !collapsed.has(file.path)
          return (
            <div key={file.path} className="search-file">
              <FileItem path={file.path} onMention={onMention} onClick={() => fold(file.path)} expanded={open}>
                {open ? <ChevronDown size={13} className="file-icon" /> : <ChevronRight size={13} className="file-icon" />}
                <Icon size={15} className="file-icon" />
                <span className="file-name">
                  {name}
                  <span className="file-dir">{file.path.slice(0, -name.length)}</span>
                </span>
                {file.capped ? (
                  <span className="search-count capped" data-tip={`Only the first ${result.per_file_limit} matching lines are shown`}>
                    {file.matches.length}+
                  </span>
                ) : <span className="search-count">{file.matches.length}</span>}
              </FileItem>
              {open ? file.matches.map((match) => (
                <button
                  type="button"
                  key={match.line}
                  className="search-line"
                  onClick={() => onOpen(file.path, match.line)}
                  title={`${file.path}:${match.line}`}
                >
                  <span className="ln">{match.line}</span>
                  <code>
                    {match.clipped ? '…' : null}
                    <Highlighted text={match.text} ranges={utf16Ranges(match.text, match.ranges)} />
                  </code>
                </button>
              )) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
})
