import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { stopProcess } from '../lib/api'
import type { ProcessActivity } from '../lib/protocol'
import { logSegments, ProcessCommand, ProcessViewer } from './ProcessViewer'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  stopProcess: vi.fn(async () => ({ ok: true })),
  readProcessOutput: vi.fn(async () => ({ start: 0, end: 0, size: 0, text: '' })),
}))

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

describe('logSegments', () => {
  it('splits JSON records from plain runs and keeps the exact text', () => {
    const text = 'start\nmore\n{"a": 1}\n{"b": [true]}\nend'
    const segments = logSegments(text)
    expect(segments.map((segment) => segment.json)).toEqual([false, true, true, false])
    const joined = segments.map((segment) => segment.json ? segment.tokens.map((token) => token.text).join('') : segment.text).join('')
    expect(joined).toBe(text)
  })
})

describe('ProcessViewer', () => {
  it('keeps the stop button armed again soon after for the full delay', async () => {
    vi.useFakeTimers()
    try {
      const process = {
        process_id: 'p1', label: 'npm run dev', command: '', elapsed_seconds: 5, last_line: '', watching: false,
        status: 'running', exit_code: null, log_bytes: 0, events_bytes: 0, kind: 'shell',
      } as ProcessActivity
      render(
        <ProcessViewer sessionId="s1" process={process} receivedAt={Date.now()} connected onClose={() => {}} onOpenFile={() => {}} />,
      )
      const stop = () => screen.getByRole('button', { name: /stop/i })
      fireEvent.click(stop())
      act(() => { vi.advanceTimersByTime(1000) })
      await act(async () => { fireEvent.click(stop()) })
      expect(stopProcess).toHaveBeenCalledTimes(1)
      // Still running; arm again 2 s after the first arm.
      act(() => { vi.advanceTimersByTime(1000) })
      fireEvent.click(stop())
      act(() => { vi.advanceTimersByTime(1500) })
      expect(stop()).toHaveAccessibleName('Click again to stop')
    } finally {
      vi.useRealTimers()
    }
  })
})
