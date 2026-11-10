import { describe, expect, it, vi } from 'vitest'

import { QueryCache, narrowMatches, pathMatchRanges, splitRanges, subsequenceRanges } from './search'

describe('subsequenceRanges', () => {
  it('merges adjacent hits and ignores case', () => {
    expect(subsequenceRanges('WidgetView.py', 'wiv')).toEqual([[0, 2], [6, 7]])
  })

  it('returns null when the query is not a subsequence', () => {
    expect(subsequenceRanges('abc', 'abd')).toBeNull()
  })
})

describe('pathMatchRanges', () => {
  it('prefers the basename', () => {
    expect(pathMatchRanges('ene/web/web.ts', 'web')).toEqual([[8, 11]])
  })

  it('falls back to the whole path', () => {
    expect(pathMatchRanges('ene/hub/files.py', 'hubfi')).toEqual([[4, 7], [8, 10]])
  })

  it('is empty for a blank query', () => {
    expect(pathMatchRanges('a/b', '  ')).toEqual([])
  })
})

describe('splitRanges', () => {
  it('splits text into plain and highlighted parts', () => {
    expect(splitRanges('hello world', [[6, 11], [0, 1]])).toEqual([
      { text: 'h', hit: true },
      { text: 'ello ', hit: false },
      { text: 'world', hit: true },
    ])
  })

  it('clamps out-of-range and overlapping ranges', () => {
    expect(splitRanges('abc', [[1, 9], [2, 3]])).toEqual([
      { text: 'a', hit: false },
      { text: 'bc', hit: true },
    ])
  })
})

describe('narrowMatches', () => {
  const items = ['ene/terminal.py', 'ene/term.ts', 'docs/readme.md']
  it('filters results of a prefix query by subsequence', () => {
    expect(narrowMatches(items, (item) => item, 'te', 'tera')).toEqual(['ene/terminal.py'])
    expect(narrowMatches(items, (item) => item, 'te', 'te')).toBe(items)
  })
  it('gives up when the query does not extend the base', () => {
    expect(narrowMatches(items, (item) => item, 'term', 'ter')).toBeNull()
  })
})

describe('QueryCache', () => {
  it('expires entries and evicts the oldest past its size', () => {
    vi.useFakeTimers()
    try {
      const cache = new QueryCache<number>(1_000, 2)
      cache.set('a', 1)
      cache.set('b', 2)
      cache.set('c', 3)
      expect(cache.get('a')).toBeUndefined()
      expect(cache.get('c')).toBe(3)
      vi.advanceTimersByTime(1_000)
      expect(cache.get('c')).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })
})
