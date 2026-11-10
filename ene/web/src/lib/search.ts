import { useEffect, useState } from 'react'

export type Range = [number, number]

/**
 * Character ranges of a greedy, case-insensitive subsequence match of
 * `query` in `text` starting at `from`, with adjacent hits merged; null when
 * `query` is not a subsequence.
 */
export function subsequenceRanges(text: string, query: string, from = 0): Range[] | null {
  const haystack = text.toLowerCase()
  const needle = query.toLowerCase()
  const ranges: Range[] = []
  let index = 0
  for (let i = from; i < haystack.length && index < needle.length; i++) {
    if (haystack[i] !== needle[index]) continue
    const last = ranges.at(-1)
    if (last && last[1] === i) last[1] = i + 1
    else ranges.push([i, i + 1])
    index++
  }
  return index === needle.length ? ranges : null
}

/** Highlight for a fuzzy path hit: the basename first, like the hub's scorer. */
export function pathMatchRanges(path: string, query: string): Range[] {
  const needle = query.trim()
  if (!needle) return []
  return subsequenceRanges(path, needle, path.lastIndexOf('/') + 1) ?? subsequenceRanges(path, needle) ?? []
}

/** Split `text` into plain and highlighted parts by sorted, non-overlapping ranges. */
export function splitRanges(text: string, ranges: Range[]): Array<{ text: string; hit: boolean }> {
  const parts: Array<{ text: string; hit: boolean }> = []
  let cursor = 0
  for (const [rawStart, rawEnd] of [...ranges].sort((a, b) => a[0] - b[0])) {
    const start = Math.max(cursor, Math.min(rawStart, text.length))
    const end = Math.max(start, Math.min(rawEnd, text.length))
    if (start > cursor) parts.push({ text: text.slice(cursor, start), hit: false })
    if (end > start) parts.push({ text: text.slice(start, end), hit: true })
    cursor = end
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), hit: false })
  return parts
}

/** `value`, updated only after it has stopped changing for `delay` ms. */
export function useDebounced<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])
  return settled
}

export type FilesFocus = 'find' | 'search'

const FILES_FOCUS_EVENT = 'ene:files-focus'
let pendingFocus: FilesFocus | null = null

/**
 * Ask the Files panel to focus its finder or text search. The panel may not
 * be mounted yet (the shortcut also opens it), so the request is kept until
 * one picks it up.
 */
export function requestFilesFocus(mode: FilesFocus) {
  pendingFocus = mode
  window.dispatchEvent(new CustomEvent<FilesFocus>(FILES_FOCUS_EVENT, { detail: mode }))
}

/** Calls `handler` for pending and future focus requests. */
export function useFilesFocus(handler: (mode: FilesFocus) => void) {
  useEffect(() => {
    if (pendingFocus) {
      handler(pendingFocus)
      pendingFocus = null
    }
    const listener = (event: Event) => {
      pendingFocus = null
      handler((event as CustomEvent<FilesFocus>).detail)
    }
    window.addEventListener(FILES_FOCUS_EVENT, listener)
    return () => window.removeEventListener(FILES_FOCUS_EVENT, listener)
  }, [handler])
}
