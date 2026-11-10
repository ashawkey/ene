import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Markdown, OpenFileContext, SessionIdContext } from './Markdown'

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

describe('Markdown images', () => {
  const renderIn = (text: string, open = vi.fn()) => render(
    <SessionIdContext.Provider value="abc">
      <OpenFileContext.Provider value={open}>
        <Markdown text={text} />
      </OpenFileContext.Provider>
    </SessionIdContext.Provider>,
  )

  it('renders local raster images through the hub and opens them in the viewer', () => {
    const open = vi.fn()
    renderIn('![plot](out/fig.png) ![abs](file:///tmp/a%20b.jpg)', open)
    const plot = screen.getByAltText('plot')
    expect(plot.getAttribute('src')).toBe('/api/sessions/abc/raw?path=out%2Ffig.png')
    fireEvent.click(plot)
    expect(open).toHaveBeenCalledWith('out/fig.png')
    expect(screen.getByText('[abs]')).toBeTruthy() // whitespace is not a path
  })

  it('keeps remote, non-raster, and failed images as alt text', () => {
    const { container } = renderIn('![web](https://example.com/a.png) ![vec](a/b.svg) ![gone](a/missing.png)')
    expect(container.querySelectorAll('img[alt="web"], img[alt="vec"]')).toHaveLength(0)
    fireEvent.error(screen.getByAltText('gone'))
    expect(container.textContent).toContain('[web]')
    expect(container.textContent).toContain('[vec]')
    expect(container.textContent).toContain('[gone]')
  })

  it('shows alt text outside a session', () => {
    render(<Markdown text="![plot](out/fig.png)" />)
    expect(screen.getByText('[plot]')).toBeTruthy()
  })
})
