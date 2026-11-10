import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { pathCompletions } from '../lib/api'
import { Composer } from './Composer'

vi.mock('../lib/api', () => ({ pathCompletions: vi.fn() }))

function Input() {
  const [draft, setDraft] = useState('')
  return (
    <Composer
      sessionId="s1"
      draft={draft}
      onDraft={setDraft}
      onSubmit={() => true}
      onCancel={() => {}}
      onWithdraw={() => {}}
      busy={false}
      connected
      commands={{ help: 'Help' }}
      history={[]}
      pending={null}
      outbox={[]}
      withdrawing={false}
      indicator={null}
      operationSince={null}
      focusSignal={0}
    />
  )
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(pathCompletions).mockResolvedValue(['@ene/'])
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('composer completion', () => {
  it('shows commands immediately and requests files after 50 ms', async () => {
    render(<Input />)
    const field = screen.getByRole('textbox', { name: 'Message' })
    fireEvent.change(field, { target: { value: '/' } })
    expect(screen.getByRole('option', { name: '/help Help' })).toBeInTheDocument()
    expect(pathCompletions).not.toHaveBeenCalled()

    fireEvent.change(field, { target: { value: '@' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(49) })
    expect(pathCompletions).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(pathCompletions).toHaveBeenCalledWith('s1', '')
    expect(screen.getByRole('option', { name: 'ene/' })).toBeInTheDocument()
  })

  it('debounces file lookup while typing', async () => {
    render(<Input />)
    const field = screen.getByRole('textbox', { name: 'Message' })
    fireEvent.change(field, { target: { value: '@' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(25) })
    fireEvent.change(field, { target: { value: '@e' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(49) })
    expect(pathCompletions).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(pathCompletions).toHaveBeenCalledExactlyOnceWith('s1', 'e')
  })
})
