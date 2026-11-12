import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import type { ComponentProps } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { pathCompletions, sessionOptions } from '../lib/api'
import { Composer } from './Composer'

vi.mock('../lib/api', () => ({ pathCompletions: vi.fn(), sessionOptions: vi.fn() }))

function Input(props: Partial<ComponentProps<typeof Composer>>) {
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
      runningProcesses={0}
      onProcesses={() => {}}
      focusSignal={0}
      cwd="/work"
      model="fast"
      onModel={() => {}}
      onEffort={() => {}}
      {...props}
    />
  )
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(pathCompletions).mockResolvedValue(['@ene/'])
  // Model choices load only in the picker tests, so other updates stay inside act().
  vi.mocked(sessionOptions).mockReturnValue(new Promise(() => {}))
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

describe('composer status strip', () => {
  it('shows running background processes while idle', () => {
    const onProcesses = vi.fn()
    render(<Input runningProcesses={2} onProcesses={onProcesses} />)
    fireEvent.click(screen.getByText('2 running'))
    expect(onProcesses).toHaveBeenCalled()
  })

  it('hides when idle without processes', () => {
    const { container } = render(<Input />)
    expect(container.querySelector('.status-strip')).toBeNull()
  })

  it('shows the live output rate while streaming', () => {
    const indicator = { label: 'Working', input_tokens: 10, output_tokens: 2, tps: 42.5, receivedAt: Date.now() }
    render(<Input busy indicator={indicator} operationSince={Date.now() / 1000} />)
    expect(screen.getByText('~42.5 tok/s')).toBeTruthy()
  })
})

describe('queued message chip', () => {
  it('marks a plain queued message as joining the round', () => {
    const { container } = render(<Input pending={{ id: 'p', text: 'hi', source: 'web', steer: true }} />)
    expect(container.querySelector('.queue-chip.steering')).not.toBeNull()
    expect(container.querySelector('.queue-delay')).toBeNull()
  })

  it('marks a /wait message and shows its remaining delay', () => {
    vi.setSystemTime(new Date(1_000_000))
    const { container, rerender } = render(
      <Input pending={{ id: 'p', text: 'later', source: 'web', steer: false, ready_at: 1000 + 90 }} />,
    )
    expect(container.querySelector('.queue-chip.waiting')).not.toBeNull()
    expect(screen.getByText('(1m 30s)')).toBeTruthy()
    rerender(<Input pending={{ id: 'p', text: 'later', source: 'web', steer: false, ready_at: null }} />)
    expect(container.querySelector('.queue-chip.waiting')).not.toBeNull()
    expect(container.querySelector('.queue-delay')).toBeNull()
  })
})

describe('model picker', () => {
  beforeEach(() => {
    vi.mocked(sessionOptions).mockResolvedValue({ models: ['fast', 'smart'], default_model: 'fast', personas: [], reasoning_efforts: ['low', 'high'] })
  })

  it('sets reasoning effort through its select, even mid-round', async () => {
    const onEffort = vi.fn()
    render(<Input effort="high" onEffort={onEffort} busy />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const select = screen.getByRole('combobox', { name: 'Reasoning effort' }) as HTMLSelectElement
    expect(select.value).toBe('high')
    expect(Array.from(select.options).map((option) => option.value)).toEqual(['low', 'high'])
    expect(select.disabled).toBe(false)
    fireEvent.change(select, { target: { value: 'low' } })
    expect(onEffort).toHaveBeenCalledWith('low')
  })

  it('hides the effort picker when the model has no effort control', async () => {
    render(<Input />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.queryByRole('combobox', { name: 'Reasoning effort' })).toBeNull()
  })

  it('switches models through the select', async () => {
    const onModel = vi.fn()
    render(<Input onModel={onModel} modelDetail="gpt-fast" />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('fast (gpt-fast)')).toBeTruthy()
    const select = screen.getByRole('combobox', { name: 'Model' }) as HTMLSelectElement
    expect(select.value).toBe('fast')
    expect(Array.from(select.options).map((option) => option.value)).toEqual(['fast', 'smart'])
    fireEvent.change(select, { target: { value: 'smart' } })
    expect(onModel).toHaveBeenCalledWith('smart')
  })

  it('keeps an unlisted current model and locks during a round', async () => {
    render(<Input model="custom" busy />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const select = screen.getByRole('combobox', { name: 'Model' }) as HTMLSelectElement
    expect(Array.from(select.options).map((option) => option.value)).toEqual(['custom', 'fast', 'smart'])
    expect(select.disabled).toBe(true)
  })
})

describe('composer keyboard navigation', () => {
  const setup = (history: string[], text = '') => {
    render(<Input history={history} />)
    const field = screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement
    if (text) fireEvent.change(field, { target: { value: text } })
    return field
  }
  const place = (field: HTMLTextAreaElement, start: number, end = start) => field.setSelectionRange(start, end)
  const flush = () => act(() => { vi.runOnlyPendingTimers() })

  it('moves within a multi-line draft before recalling history', () => {
    const field = setup(['older', 'newest'], 'one\ntwo')
    place(field, 6)
    expect(fireEvent.keyDown(field, { key: 'ArrowUp' })).toBe(true)
    expect(field.value).toBe('one\ntwo')

    place(field, 2)
    expect(fireEvent.keyDown(field, { key: 'ArrowUp' })).toBe(false)
    expect(field.value).toBe('newest')
  })

  it('walks history with the caret on the top line going up and restores the draft', () => {
    const field = setup(['a\nb', 'newest'], 'draft')
    place(field, 5)
    fireEvent.keyDown(field, { key: 'ArrowUp' })
    flush()
    fireEvent.keyDown(field, { key: 'ArrowUp' })
    flush()
    expect(field.value).toBe('a\nb')
    expect(field.selectionStart).toBe(1)

    // Down from the first line moves within the entry, then from its end.
    expect(fireEvent.keyDown(field, { key: 'ArrowDown' })).toBe(true)
    place(field, 3)
    fireEvent.keyDown(field, { key: 'ArrowDown' })
    flush()
    expect(field.value).toBe('newest')
    fireEvent.keyDown(field, { key: 'ArrowDown' })
    expect(field.value).toBe('draft')
  })

  it('leaves Down alone when not browsing history', () => {
    const field = setup(['newest'], 'draft')
    place(field, 5)
    expect(fireEvent.keyDown(field, { key: 'ArrowDown' })).toBe(true)
    expect(field.value).toBe('draft')
  })

  it('sends Home and End to the line edges, then the text edges', () => {
    const field = setup([], 'one\ntwo\nthree')
    place(field, 6)
    fireEvent.keyDown(field, { key: 'Home' })
    expect(field.selectionStart).toBe(4)
    fireEvent.keyDown(field, { key: 'Home' })
    expect(field.selectionStart).toBe(0)

    place(field, 5)
    fireEvent.keyDown(field, { key: 'End' })
    expect(field.selectionStart).toBe(7)
    fireEvent.keyDown(field, { key: 'End' })
    expect(field.selectionStart).toBe(13)
  })

  it('extends the selection with Shift+Home and Shift+End', () => {
    const field = setup([], 'one\ntwo')
    place(field, 6)
    fireEvent.keyDown(field, { key: 'Home', shiftKey: true })
    expect([field.selectionStart, field.selectionEnd, field.selectionDirection]).toEqual([4, 6, 'backward'])
    fireEvent.keyDown(field, { key: 'End', shiftKey: true })
    expect([field.selectionStart, field.selectionEnd]).toEqual([6, 7])
  })
})
