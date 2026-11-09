import { describe, expect, it } from 'vitest'

import { fileTarget, splitLinks } from './links'

describe('fileTarget', () => {
  it('accepts workspace paths with optional locations', () => {
    expect(fileTarget('ene/web/src/App.tsx')).toEqual({ path: 'ene/web/src/App.tsx', line: null })
    expect(fileTarget('ene/ui.py:42')).toEqual({ path: 'ene/ui.py', line: 42 })
    expect(fileTarget('src/a.ts#L10-L20')).toEqual({ path: 'src/a.ts', line: 10 })
    expect(fileTarget('/home/me/proj/main.go:3:7')).toEqual({ path: '/home/me/proj/main.go', line: 3 })
    expect(fileTarget('./pyproject.toml')?.path).toBe('./pyproject.toml')
    expect(fileTarget('README.md')?.path).toBe('README.md')
    expect(fileTarget('Makefile')?.path).toBe('Makefile')
  })

  it('rejects code and prose that only look path-like', () => {
    for (const text of ['os.path', 'self.value', 'foo()', 'a b.py', 'v1.2', 'src/', 'https://x.io/a.py', 'e.g', '1/2.5']) {
      expect(fileTarget(text), text).toBeNull()
    }
  })
})

describe('splitLinks', () => {
  it('finds URLs next to CJK text and trims trailing punctuation', () => {
    expect(splitLinks('见https://example.com/a?b=1。然后')).toEqual([
      { kind: 'text', text: '见' },
      { kind: 'url', text: 'https://example.com/a?b=1', href: 'https://example.com/a?b=1' },
      { kind: 'text', text: '。然后' },
    ])
    expect(splitLinks('(see https://en.wikipedia.org/wiki/Foo_(bar)).')[1]).toMatchObject({
      text: 'https://en.wikipedia.org/wiki/Foo_(bar)',
    })
  })

  it('links prose paths only when they include a directory', () => {
    expect(splitLinks('Edited ene/ui.py:12, not Node.js.')).toEqual([
      { kind: 'text', text: 'Edited ' },
      { kind: 'file', text: 'ene/ui.py:12', href: 'ene/ui.py:12' },
      { kind: 'text', text: ', not Node.js.' },
    ])
    expect(splitLinks('and/or 3/4 km/h')).toEqual([{ kind: 'text', text: 'and/or 3/4 km/h' }])
  })
})
