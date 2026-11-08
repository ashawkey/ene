import { describe, expect, it } from 'vitest'

import { splitBlocks } from './Markdown'

describe('splitBlocks', () => {
  it('splits top-level blocks at blank lines', () => {
    expect(splitBlocks('# Title\n\nPara one\nstill one\n\nPara two')).toEqual(['# Title', 'Para one\nstill one', 'Para two'])
  })

  it('keeps fenced code with blank lines in one block', () => {
    const text = 'Intro\n\n```py\na = 1\n\nb = 2\n```\n\nAfter'
    expect(splitBlocks(text)).toEqual(['Intro', '```py\na = 1\n\nb = 2\n```', 'After'])
  })

  it('keeps an unterminated fence (still streaming) together', () => {
    expect(splitBlocks('```\na\n\nb')).toEqual(['```\na\n\nb'])
  })

  it('does not split before indented continuations', () => {
    expect(splitBlocks('- item\n\n  more of item\n\nNext')).toEqual(['- item\n\n  more of item', 'Next'])
  })

  it('leaves earlier blocks unchanged as text grows', () => {
    const before = splitBlocks('A\n\nB')
    const after = splitBlocks('A\n\nB more\n\nC')
    expect(after[0]).toBe(before[0])
  })
})
