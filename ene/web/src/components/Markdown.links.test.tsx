import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Markdown, OpenFileContext } from './Markdown'

describe('Markdown links', () => {
  it('opens file paths in the viewer without changing the text', () => {
    const open = vi.fn()
    const { container } = render(
      <OpenFileContext.Provider value={open}>
        <Markdown text={'Changed `ene/ui.py:12` and src/app.ts; see [docs](docs/index.md) or https://example.com.'} />
      </OpenFileContext.Provider>,
    )
    expect(container.textContent).toBe('Changed ene/ui.py:12 and src/app.ts; see docs or https://example.com.')
    fireEvent.click(screen.getByText('ene/ui.py:12'))
    fireEvent.click(screen.getByText('src/app.ts'))
    fireEvent.click(screen.getByText('docs'))
    expect(open.mock.calls.map((call) => call[0])).toEqual(['ene/ui.py', 'src/app.ts', 'docs/index.md'])
    const web = screen.getByText('https://example.com')
    expect(web.closest('a')?.getAttribute('href')).toBe('https://example.com')
  })

  it('leaves paths as plain text outside a session timeline', () => {
    render(<Markdown text="`ene/ui.py`" />)
    expect(screen.getByText('ene/ui.py').closest('button')).toBeNull()
  })
})
