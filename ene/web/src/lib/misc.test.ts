import { describe, expect, it } from 'vitest'

import { parseAnsi, stripAnsi } from './ansi'
import { compactNumber, duration } from './format'
import type { TimelineItem } from './session'
import { backoffDelay } from './socket'
import { groupItems } from './timeline'

describe('ansi', () => {
  it('splits styled spans', () => {
    expect(parseAnsi('a\u001b[1;31mb\u001b[0mc')).toEqual([
      { text: 'a' },
      { text: 'b', bold: true, fg: 1 },
      { text: 'c' },
    ])
    expect(stripAnsi('\u001b[2Kx\u001b[0m')).toBe('x')
  })
})

describe('format', () => {
  it('formats numbers and durations', () => {
    expect(compactNumber(950)).toBe('950')
    expect(compactNumber(12_345)).toBe('12.3k')
    expect(compactNumber(2_500_000)).toBe('2.5M')
    expect(duration(3.25)).toBe('3.3s')
    expect(duration(75)).toBe('1m 15s')
  })
})

describe('backoff', () => {
  it('grows to a jittered ceiling', () => {
    expect(backoffDelay(0, () => 0.5)).toBe(400)
    expect(backoffDelay(50, () => 0.5)).toBe(12000)
    expect(backoffDelay(50, () => 0)).toBe(9000)
  })
})

describe('grouping', () => {
  const tool = (n: number): TimelineItem => ({ kind: 'tool', key: `t${n}`, seq: n, ts: 0, name: 'x', primary: '', qualifiers: [], text: '', result: null })
  const say = (n: number): TimelineItem => ({ kind: 'assistant', key: `a${n}`, seq: n, ts: 0, text: 'hi', streaming: false })
  it('folds runs of three or more tool calls', () => {
    const nodes = groupItems([tool(1), tool(2), say(3), tool(4), tool(5), tool(6)], true)
    expect(nodes.map((node) => node.kind)).toEqual(['item', 'item', 'item', 'group'])
    expect(nodes[3]).toMatchObject({ kind: 'group', live: true })
  })
})
