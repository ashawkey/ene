import { describe, expect, it } from 'vitest'

import { pathMatchRanges, splitRanges, subsequenceRanges } from './search'

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
