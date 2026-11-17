import { describe, expect, it } from 'vitest'

import { findInElement, findInLines, gotoLine, lineRef, lineSpan, textRange } from './codeFind'

describe('find in file', () => {
  it('matches smart-cased, non-overlapping, and capped', () => {
    expect(findInLines(['Foo foo', 'xfoofoo'], 'foo').matches).toEqual([
      { line: 0, start: 0, end: 3 },
      { line: 0, start: 4, end: 7 },
      { line: 1, start: 1, end: 4 },
      { line: 1, start: 4, end: 7 },
    ])
    expect(findInLines(['Foo foo'], 'Foo').matches).toEqual([{ line: 0, start: 0, end: 3 }])
    expect(findInLines(['aaaa'], 'aa').matches).toHaveLength(2)
    expect(findInLines(['a a a'], 'a', 2)).toEqual({ matches: [{ line: 0, start: 0, end: 1 }, { line: 0, start: 2, end: 3 }], truncated: true })
    expect(findInLines(['x'], '').matches).toEqual([])
  })

  it('reads go-to-line queries and formats line references', () => {
    expect(gotoLine(':42')).toBe(42)
    expect(gotoLine(': 7 ')).toBe(7)
    expect(gotoLine(':0')).toBeNull()
    expect(gotoLine('42')).toBeNull()
    expect(lineSpan({ anchor: 9, head: 3 })).toEqual([3, 9])
    expect(lineRef('a.py', 3, 3)).toBe('a.py:3')
    expect(lineRef('a.py', 3, 9)).toBe('a.py:3-9')
  })

  it('maps character offsets across highlighted spans', () => {
    const code = document.createElement('code')
    code.innerHTML = '<span>def</span> <span>foo</span>()'
    expect(textRange(code, 4, 7)?.toString()).toBe('foo')
    expect(textRange(code, 2, 6)?.toString()).toBe('f fo')
    expect(textRange(code, 8, 20)).toBeNull()
  })

  it('finds rendered text across elements', () => {
    const root = document.createElement('div')
    root.innerHTML = '<h1>Setup</h1><p>Run <b>set</b>up, then <code>SETUP</code>.</p>'
    const found = findInElement(root, 'setup')
    expect(found.ranges.map((range) => range.toString())).toEqual(['Setup', 'setup', 'SETUP'])
    expect(findInElement(root, 'SETUP').ranges.map((range) => range.toString())).toEqual(['SETUP'])
    expect(findInElement(root, 'setup', 2).truncated).toBe(true)
    expect(findInElement(root, 'missing').ranges).toEqual([])
  })
})
