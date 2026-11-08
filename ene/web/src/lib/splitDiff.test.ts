import { describe, expect, it } from 'vitest'

import { foldContext, splitLines } from './splitDiff'

describe('split diff', () => {
  it('pairs removals with additions and numbers both sides', () => {
    const lines = splitLines('a\nb\nc\n', 'a\nB\nc\nd\n')
    expect(lines.map((line) => [line.left?.kind, line.left?.no, line.right?.kind, line.right?.no])).toEqual([
      ['ctx', 1, 'ctx', 1],
      ['del', 2, 'add', 2],
      ['ctx', 3, 'ctx', 3],
      [undefined, undefined, 'add', 4],
    ])
  })

  it('folds unchanged runs away from changes', () => {
    const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join('\n')
    const after = before.replace('l10', 'changed')
    const blocks = foldContext(splitLines(before, after))
    expect(blocks.map((block) => [block.kind, block.lines.length])).toEqual([
      ['gap', 7], ['lines', 3], ['lines', 1], ['lines', 3], ['gap', 6],
    ])
  })

  it('handles added and deleted files', () => {
    expect(splitLines('', 'x\ny\n').every((line) => line.left === null && line.right?.kind === 'add')).toBe(true)
    expect(splitLines('x\n', '').map((line) => [line.left?.kind, line.right])).toEqual([['del', null]])
  })
})
