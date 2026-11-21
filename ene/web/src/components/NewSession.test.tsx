import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { browseDirectories, conversations, createSession, makeDirectory, recentWorkspaces, sessionOptions } from '../lib/api'
import type { ConversationSummary, SessionSummary } from '../lib/protocol'
import { NewSessionDialog } from './NewSession'

vi.mock('../lib/api', () => ({
  browseDirectories: vi.fn(),
  conversations: vi.fn(),
  createSession: vi.fn(),
  errorMessage: (_exc: unknown, fallback: string) => fallback,
  makeDirectory: vi.fn(),
  recentWorkspaces: vi.fn(),
  sessionOptions: vi.fn(),
}))

const NOW = new Date(2026, 10, 20, 12).getTime() / 1000
const conversation = (id: string, name: string, updated_at: number): ConversationSummary => ({
  id, name, message_count: 3, round_id: 1, last_user_message: `about ${name}`, updated_at, live: false,
})
const saved = conversation('conv-a', 'old work', NOW - 3600)

beforeEach(() => {
  vi.useFakeTimers({ now: NOW * 1000 })
  vi.mocked(recentWorkspaces).mockResolvedValue([])
  vi.mocked(browseDirectories).mockImplementation(async (path) => ({ path: path || '/home', parent: '/', entries: [] }))
  vi.mocked(sessionOptions).mockResolvedValue({
    models: ['fast', 'deep'],
    default_model: 'fast',
    personas: ['chat', 'coder'],
    default_persona: 'coder',
    reasoning_efforts: ['low', 'medium', 'high'],
    default_reasoning_efforts: { fast: 'high', deep: 'medium' },
  })
  vi.mocked(conversations).mockImplementation(async (cwd) => (cwd === '/a' ? [saved] : []))
  vi.mocked(createSession).mockResolvedValue({ id: 's1' } as SessionSummary)
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
  localStorage.clear()
})

async function openAt(cwd: string) {
  render(<NewSessionDialog onClose={() => {}} onCreated={() => {}} />)
  fireEvent.change(screen.getByPlaceholderText('/path/to/project'), { target: { value: cwd } })
  await act(async () => { await vi.advanceTimersByTimeAsync(250) })
}

const start = async () => {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^(Start|Resume)$/ })) })
}

describe('new session dialog', () => {
  it('notes recent folders and folder options while they load', async () => {
    let resolve: (list: string[]) => void = () => {}
    vi.mocked(recentWorkspaces).mockReturnValue(new Promise<string[]>((done) => { resolve = done }))
    render(<NewSessionDialog onClose={() => {}} onCreated={() => {}} />)
    expect(screen.getByText('Loading recent folders…')).toBeInTheDocument()
    await act(async () => resolve([]))
    expect(screen.queryByText('Loading recent folders…')).toBeNull()
    fireEvent.change(screen.getByPlaceholderText('/path/to/project'), { target: { value: '/a' } })
    expect(screen.getByText('loading…')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(screen.queryByText('loading…')).toBeNull()
  })

  it('makes persona and reasoning explicit, following the model', async () => {
    await openAt('/b')
    const persona = screen.getByRole('combobox', { name: /Persona/ }) as HTMLSelectElement
    const effort = screen.getByRole('combobox', { name: /Reasoning/ }) as HTMLSelectElement
    expect([...persona.options].map((option) => option.value)).toEqual(['chat', 'coder'])
    expect(persona.value).toBe('coder')
    expect(effort.value).toBe('high')

    fireEvent.change(screen.getByRole('combobox', { name: /Model/ }), { target: { value: 'deep' } })
    expect(effort.value).toBe('medium')
    await start()
    expect(createSession).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ model: 'deep', persona: 'coder', reasoning_effort: 'medium' }))
  })

  it('resumes the conversation picked for the working directory', async () => {
    await openAt('/a')
    fireEvent.click(screen.getByRole('button', { name: /old work/ }))
    await start()
    expect(createSession).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ cwd: '/a', resume: 'conv-a' }))
  })

  it('groups conversations by date and searches their titles', async () => {
    vi.mocked(conversations).mockResolvedValue([
      conversation('c1', 'parser fix', NOW - 60),
      conversation('c2', 'release notes', NOW - 86_400),
      conversation('c3', 'parser docs', NOW - 40 * 86_400),
    ])
    await openAt('/c')
    expect(screen.getAllByRole('heading', { level: 3 }).map((item) => item.textContent)).toEqual(['Today', 'Yesterday', 'October'])

    fireEvent.change(screen.getByRole('textbox', { name: 'Search conversations' }), { target: { value: 'PARSER' } })
    expect(screen.queryByRole('button', { name: /release notes/ })).toBeNull()
    expect(screen.getByRole('button', { name: /parser fix/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /parser docs/ })).toBeInTheDocument()
  })

  it('drops a picked conversation when the working directory changes', async () => {
    await openAt('/a')
    fireEvent.click(screen.getByRole('button', { name: /old work/ }))

    fireEvent.change(screen.getByPlaceholderText('/path/to/project'), { target: { value: '/b' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(250) })
    expect(screen.getByRole('button', { name: 'New conversation' })).toHaveAttribute('aria-pressed', 'true')
    await start()
    expect(createSession).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ cwd: '/b', resume: '' }))
  })

  it('creates a folder in the browsed directory and uses it', async () => {
    vi.mocked(makeDirectory).mockResolvedValue('/b/fresh')
    await openAt('/b')
    fireEvent.click(screen.getByRole('button', { name: 'New folder' }))
    const field = screen.getByRole('textbox', { name: 'New folder name' })
    fireEvent.change(field, { target: { value: 'fresh' } })
    await act(async () => { fireEvent.keyDown(field, { key: 'Enter' }) })

    expect(makeDirectory).toHaveBeenCalledExactlyOnceWith('/b', 'fresh')
    expect(screen.getByPlaceholderText('/path/to/project')).toHaveValue('/b/fresh')
    expect(createSession).not.toHaveBeenCalled()
  })

  it('offers to create a typed folder that does not exist', async () => {
    vi.mocked(browseDirectories).mockImplementation(async (path) => {
      if (path === '/b/missing') throw new Error('missing')
      return { path, parent: '/', entries: [] }
    })
    vi.mocked(makeDirectory).mockResolvedValue('/b/missing')
    await openAt('/b/missing')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Create “missing”/ })) })
    expect(makeDirectory).toHaveBeenCalledExactlyOnceWith('/b/', 'missing')
  })
})
