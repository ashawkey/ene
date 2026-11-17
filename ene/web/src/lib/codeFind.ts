/** Find-in-file, go-to-line, and line-range helpers for the file viewer. */

/** One match: 0-based line index and character [start, end) within it. */
export type LineMatch = { line: number; start: number; end: number }

export const MAX_FIND_MATCHES = 5000

/**
 * Plain-text matches of *query* in *lines*, smart-cased: case-insensitive
 * unless the query has an uppercase letter.
 */
export function findInLines(lines: string[], query: string, limit = MAX_FIND_MATCHES): { matches: LineMatch[]; truncated: boolean } {
  const matches: LineMatch[] = []
  if (!query) return { matches, truncated: false }
  for (let line = 0; line < lines.length; line++) {
    for (const start of matchStarts(lines[line], query, limit - matches.length + 1)) {
      if (matches.length >= limit) return { matches, truncated: true }
      matches.push({ line, start, end: start + query.length })
    }
  }
  return { matches, truncated: false }
}

/** Start offsets of non-overlapping, smart-cased matches of *query* in *text*, at most *limit*. */
function matchStarts(text: string, query: string, limit: number): number[] {
  const sensitive = query !== query.toLowerCase()
  const haystack = sensitive ? text : text.toLowerCase()
  const needle = sensitive ? query : query.toLowerCase()
  const starts: number[] = []
  for (let from = 0; starts.length < limit;) {
    const start = haystack.indexOf(needle, from)
    if (start < 0) break
    starts.push(start)
    from = start + needle.length
  }
  return starts
}

/**
 * Matches of *query* in the rendered text of *root* (rendered Markdown, say),
 * as DOM ranges; a match may span elements (`foo **bar**`).
 */
export function findInElement(root: Node, query: string, limit = MAX_FIND_MATCHES): { ranges: Range[]; truncated: boolean } {
  const ranges: Range[] = []
  if (!query) return { ranges, truncated: false }
  const nodes: Text[] = []
  const offsets: number[] = []
  let text = ''
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    nodes.push(node as Text)
    offsets.push(text.length)
    text += node.textContent ?? ''
  }
  // The text node holding character *at* (the last one starting at or before it).
  const locate = (at: number, end: boolean): [Text, number] => {
    let low = 0
    let high = nodes.length - 1
    while (low < high) {
      const mid = (low + high + 1) >> 1
      // An end offset belongs to the node it closes, not the next one.
      if (offsets[mid] < at || (!end && offsets[mid] === at)) low = mid
      else high = mid - 1
    }
    return [nodes[low], at - offsets[low]]
  }
  const starts = matchStarts(text, query, limit + 1)
  for (const start of starts.slice(0, limit)) {
    const range = document.createRange()
    range.setStart(...locate(start, false))
    range.setEnd(...locate(start + query.length, true))
    ranges.push(range)
  }
  return { ranges, truncated: starts.length > limit }
}

/** The 1-based line a `:123` query asks for, or null. */
export function gotoLine(query: string): number | null {
  const match = /^:\s*(\d+)\s*$/.exec(query)
  return match ? Number(match[1]) || null : null
}

/** First and last line of a selection, in order. */
export function lineSpan(selection: { anchor: number; head: number }): [number, number] {
  return selection.anchor <= selection.head ? [selection.anchor, selection.head] : [selection.head, selection.anchor]
}

/** `path:12` or `path:12-20`, the form file links and mentions use. */
export function lineRef(path: string, first: number, last: number) {
  return last > first ? `${path}:${first}-${last}` : `${path}:${first}`
}

/** A DOM Range over characters [start, end) of *element*'s text, or null. */
export function textRange(element: Node, start: number, end: number): Range | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  const range = document.createRange()
  let offset = 0
  let started = false
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const length = node.textContent?.length ?? 0
    if (!started && start < offset + length) {
      range.setStart(node, start - offset)
      started = true
    }
    if (started && end <= offset + length) {
      range.setEnd(node, end - offset)
      return range
    }
    offset += length
  }
  return null
}

/** Whether the browser can paint ranges without touching the DOM (CSS Custom Highlight API). */
export const canHighlight = () => typeof CSS !== 'undefined' && 'highlights' in CSS && typeof Highlight !== 'undefined'
