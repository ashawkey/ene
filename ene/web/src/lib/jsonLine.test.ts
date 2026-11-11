import { describe, expect, it } from 'vitest'

import { jsonLineTokens } from './jsonLine'

const kinds = (line: string) => jsonLineTokens(line)?.filter((token) => token.kind !== 'plain').map((token) => `${token.kind}:${token.text}`)

describe('jsonLineTokens', () => {
  it('tokenizes a JSON Lines record', () => {
    expect(kinds('{"step": 3, "loss": -1.5e-3, "ok": true, "tag": "a\\"b:", "x": null}')).toEqual([
      'punct:{', 'key:"step"', 'punct::', 'number:3', 'punct:,', 'key:"loss"', 'punct::', 'number:-1.5e-3', 'punct:,',
      'key:"ok"', 'punct::', 'literal:true', 'punct:,', 'key:"tag"', 'punct::', 'string:"a\\"b:"', 'punct:,',
      'key:"x"', 'punct::', 'literal:null', 'punct:}',
    ])
  })

  it('keeps the exact text, including a log prefix', () => {
    const line = '2026-11-10 12:00:01 INFO {"a": [1, {"b": "c"}]}  '
    const tokens = jsonLineTokens(line)!
    expect(tokens.map((token) => token.text).join('')).toBe(line)
    expect(tokens[0]).toEqual({ kind: 'plain', text: '2026-11-10 12:00:01 INFO ' })
    expect(jsonLineTokens('[INFO] {"a": 1}')?.[0]).toEqual({ kind: 'plain', text: '[INFO] ' })
  })

  it('accepts whole-line arrays only', () => {
    expect(kinds('[1, "x"]')).toEqual(['punct:[', 'number:1', 'punct:,', 'string:"x"', 'punct:]'])
    expect(jsonLineTokens('epoch [1, 2]')).toBeNull()
  })

  it('ignores lines that are not JSON values', () => {
    for (const line of ['plain text', '{broken', '{"a": 1} trailing', '"str"', '\x1b[31m{"a":1}\x1b[0m', '[INFO] done']) {
      expect(jsonLineTokens(line)).toBeNull()
    }
  })
})
