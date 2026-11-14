import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { conversations, createSession, recentWorkspaces, sessionOptions } from '../lib/api'
import type { ConversationSummary, SessionSummary } from '../lib/protocol'
import { NewSessionDialog } from './NewSession'

vi.mock('../lib/api', () => ({
  browseDirectories: vi.fn(),
  conversations: vi.fn(),
  createSession: vi.fn(),
  errorMessage: (_exc: unknown, fallback: string) => fallback,
  recentWorkspaces: vi.fn(),
  sessionOptions: vi.fn(),
}))

const saved: ConversationSummary = { id: 'conv-a', name: 'old work', message_count: 3, round_id: 1, last_user_message: 'hi', live: false }

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(recentWorkspaces).mockResolvedValue([])
  vi.mocked(sessionOptions).mockResolvedValue({ models: ['fast'], default_model: 'fast', personas: [], reasoning_efforts: [] })
  vi.mocked(conversations).mockImplementation(async (cwd) => (cwd === '/a' ? [saved] : []))
  vi.mocked(createSession).mockResolvedValue({ id: 's1' } as SessionSummary)
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
  localStorage.clear()
})

describe('new session dialog', () => {
  it('resumes the conversation picked for the working directory', async () => {
    render(<NewSessionDialog onClose={() => {}} onCreated={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText('/path/to/project'), { target: { value: '/a' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    fireEvent.change(screen.getByRole('combobox', { name: /Resume/ }), { target: { value: 'conv-a' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Start/ })) })
    expect(createSession).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ cwd: '/a', resume: 'conv-a' }))
  })

  it('drops a picked conversation when the working directory changes', async () => {
    render(<NewSessionDialog onClose={() => {}} onCreated={() => {}} />)
    const cwd = screen.getByPlaceholderText('/path/to/project')
    fireEvent.change(cwd, { target: { value: '/a' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    fireEvent.change(screen.getByRole('combobox', { name: /Resume/ }), { target: { value: 'conv-a' } })

    fireEvent.change(cwd, { target: { value: '/b' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect((screen.getByRole('combobox', { name: /Resume/ }) as HTMLSelectElement).value).toBe('')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Start/ })) })
    expect(createSession).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ cwd: '/b', resume: '' }))
  })
})
