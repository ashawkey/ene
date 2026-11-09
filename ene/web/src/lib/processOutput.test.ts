import { describe, expect, it } from 'vitest'

import { addEvents, addLogRange, emptyFeed, emptyLog, logEnd, logLines, logStart, logText, MAX_LOG_CHARS } from './processOutput'
import type { ProcessOutput } from './protocol'

const range = (start: number, text: string, size: number): ProcessOutput => ({
  start, end: start + text.length, size, status: 'running', text,
})

describe('process log window', () => {
  it('appends follow reads and prepends earlier pages into one stretch', () => {
    let log = addLogRange(emptyLog(), range(10, 'c\nd\n', 14))
    log = addLogRange(log, range(14, 'e\n', 16))
    log = addLogRange(log, range(6, 'a\nb\n', 16))
    expect(logText(log)).toBe('a\nb\nc\nd\ne\n')
    expect([logStart(log), logEnd(log), log.size]).toEqual([6, 16, 16])
  })

  it('restarts on a disjoint range and ignores empty follow reads', () => {
    let log = addLogRange(emptyLog(), range(0, 'old\n', 4))
    const same = addLogRange(log, range(4, '', 9))
    expect(logText(same)).toBe('old\n')
    expect(same.size).toBe(9)
    log = addLogRange(log, range(100, 'tail\n', 105))
    expect(logText(log)).toBe('tail\n')
  })

  it('continues an unfinished line from the following range', () => {
    let log = addLogRange(emptyLog(), range(0, 'a\n10%\r5', 7))
    log = addLogRange(log, range(7, '0%\r100%\n', 16))
    expect(logLines(logText(log))).toEqual([
      { index: 0, text: 'a' },
      { index: 1, text: '100%' },
    ])
  })

  it('drops the far side beyond the memory cap', () => {
    const big = 'x'.repeat(MAX_LOG_CHARS / 2 - 1) + '\n'
    let log = addLogRange(emptyLog(), range(0, big, big.length * 3))
    // A 64K-merged first chunk is not merged further, so each page stays a chunk.
    log = addLogRange(log, range(big.length, big, big.length * 3))
    log = addLogRange(log, range(big.length * 2, big, big.length * 3))
    expect(logStart(log)).toBe(big.length)
    expect(logEnd(log)).toBe(big.length * 3)
  })

  it('splits lines and keeps only the last carriage-return redraw', () => {
    expect(logLines('a\r\n10%\r50%\r100%\nlast')).toEqual([
      { index: 0, text: 'a' },
      { index: 1, text: '100%' },
      { index: 2, text: 'last' },
    ])
    expect(logLines('')).toEqual([])
  })
})

describe('subagent event feed', () => {
  it('folds recorded events into a conversation and captures the result', () => {
    let feed = addEvents(emptyFeed(), {
      start: 0, end: 50, size: 80, status: 'running',
      events: [
        { seq: 1, type: 'user_message', data: { text: 'find the bug', source: 'task' } },
        { seq: 2, type: 'assistant_message', data: { text: 'found it' } },
      ],
    })
    // A page that does not continue the feed is ignored.
    expect(addEvents(feed, { start: 10, end: 20, size: 80, status: 'running', events: [] })).toBe(feed)
    feed = addEvents(feed, {
      start: 50, end: 80, size: 80, status: 'exited',
      events: [{ seq: 3, type: 'subagent_result', data: { success: true, outcome: 'completed', token_usage: { total: 1200 } } }],
    })
    expect(feed.view.items.map((item) => [item.kind, 'text' in item ? item.text : ''])).toEqual([
      ['user', 'find the bug'],
      ['assistant', 'found it'],
    ])
    expect(feed.offset).toBe(80)
    expect(feed.result).toEqual({ success: true, outcome: 'completed', error: null, tokens: 1200 })
  })
})
