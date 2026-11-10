import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ProcessCommand } from './ProcessViewer'

describe('ProcessCommand', () => {
  it('shows one line folded and the full command unfolded', () => {
    const command = `source env.sh; srun --mem=64G \\\n  python train.py ${'--flag '.repeat(80)}--last`
    const { container } = render(<ProcessCommand command={command} />)
    expect(container.querySelector('.proc-command-full')).toBeNull()
    expect(container.querySelector('.clamp')?.textContent).not.toContain('\n')

    fireEvent.click(screen.getByRole('button', { expanded: false }))
    expect(container.querySelector('.proc-command-full')?.textContent).toBe(`$ ${command}`)
    expect(screen.getByRole('button', { name: 'Copy command' })).toBeTruthy()
  })
})
