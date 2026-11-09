import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Thinking } from './TimelineItems'

const item = (text: string, streaming: boolean) => ({ kind: 'thinking' as const, key: 't', seq: 1, ts: 0, text, streaming })

describe('Thinking', () => {
  it('previews the newest text while streaming and unfolds to the whole thought', () => {
    const text = `first line\n${'x'.repeat(2000)}\nnewest line`
    const { container, rerender } = render(<Thinking item={item(text, true)} />)
    const preview = container.querySelector('.thinking-live-text')
    expect(preview?.textContent?.endsWith('newest line')).toBe(true)
    expect(preview?.textContent).not.toContain('first line')

    fireEvent.click(screen.getByRole('button', { expanded: false }))
    expect(container.querySelector('.thinking-live')).toBeNull()
    expect(container.querySelector('.thinking-text')?.textContent).toContain('first line')

    // Still open, now settled, once the stream ends.
    rerender(<Thinking item={item(text, false)} />)
    expect(container.querySelector('.thinking-text')?.textContent).toContain('newest line')
  })

  it('folds a finished thought by default', () => {
    const { container } = render(<Thinking item={item('done thinking', false)} />)
    expect(container.querySelector('.thinking-text')).toBeNull()
    expect(screen.getByText('done thinking')).toBeTruthy()
  })
})
