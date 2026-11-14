import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { CopyButton } from './Markdown'

describe('CopyButton', () => {
  it('builds lazy text only when clicked', () => {
    // The process viewer's whole loaded log (up to 4M characters) was joined
    // and stripped on every render just to have it ready for this button.
    const text = vi.fn(() => 'log text')
    const { rerender } = render(<CopyButton text={text} label="Copy loaded output" />)
    rerender(<CopyButton text={text} label="Copy loaded output" />)
    expect(text).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Copy loaded output' }))
    expect(text).toHaveBeenCalledTimes(1)
  })
})
