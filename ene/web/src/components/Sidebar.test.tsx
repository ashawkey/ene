import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../lib/protocol'
import { Sidebar } from './Sidebar'

const session = (id: string, attached_by: string): SessionSummary => ({
  id, title: '', name: `proj-${id}`, cwd: `/work/${id}`, model: 'fast', host: 'box', conversation_id: '', preview: '', attached_by, state: 'done',
})

function renderSidebar(sessions: SessionSummary[], Component = Sidebar) {
  const props = {
    sessions,
    host: 'box',
    activeId: null,
    unread: new Set<string>(),
    busyId: null,
    open: true,
    status: 'open' as const,
    theme: 'system' as const,
    searchRef: createRef<HTMLInputElement>(),
    rail: false,
    onToggleRail: vi.fn(),
    onClose: vi.fn(),
    onSelect: vi.fn(),
    onAttach: vi.fn(),
    onDetach: vi.fn(),
    onEnd: vi.fn(),
    onNew: vi.fn(),
    onTheme: vi.fn(),
    sound: false,
    onSound: vi.fn(),
    onLogout: vi.fn(),
  }
  render(<Component {...props} />)
  return props
}

const search = (text: string) => {
  const input = screen.getByPlaceholderText(/Search sessions/)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.keyDown(input, { key: 'Enter' })
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetModules()
})

describe('end session button', () => {
  it('stays armed for the full delay when armed again soon after', () => {
    vi.useFakeTimers()
    try {
      const props = renderSidebar([session('a', '')])
      const end = () => screen.getByRole('button', { name: /end session/i })
      fireEvent.click(end())
      act(() => { vi.advanceTimersByTime(1000) })
      fireEvent.click(end())
      expect(props.onEnd).toHaveBeenCalledTimes(1)
      // Ending did not take; arm again 2 s after the first arm.
      act(() => { vi.advanceTimersByTime(1000) })
      fireEvent.click(end())
      act(() => { vi.advanceTimersByTime(1500) })
      expect(end()).toHaveAccessibleName('Click again to end session')
      fireEvent.click(end())
      expect(props.onEnd).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops its disarm timer when unmounted', () => {
    vi.useFakeTimers()
    try {
      renderSidebar([session('a', '')])
      fireEvent.click(screen.getByRole('button', { name: 'End session' }))
      cleanup()
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('session search', () => {
  it('opens the first row shown on Enter, not the oldest match', () => {
    // The hub lists sessions by creation; the sidebar shows Open here first.
    const props = renderSidebar([session('a', ''), session('w', 'web')])
    search('proj')
    expect(props.onSelect).toHaveBeenCalledExactlyOnceWith('w')
    expect(props.onAttach).not.toHaveBeenCalled()
  })

  it('skips sessions owned by a terminal', () => {
    const props = renderSidebar([session('t', 'terminal'), session('a', '')])
    search('proj')
    expect(props.onAttach).toHaveBeenCalledExactlyOnceWith('a')
  })

  it('shows the search shortcut for the platform', () => {
    renderSidebar([])
    expect(screen.getByPlaceholderText(/Search sessions/)).toHaveAttribute('placeholder', 'Search sessions  Ctrl+K')
  })

  it('shows the Command key on a Mac', async () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel')
    vi.resetModules()
    const { Sidebar: MacSidebar } = await import('./Sidebar')
    renderSidebar([], MacSidebar)
    expect(screen.getByPlaceholderText(/Search sessions/)).toHaveAttribute('placeholder', 'Search sessions  ⌘K')
    expect(screen.getByRole('button', { name: 'Collapse sidebar (⌘B)' })).toBeInTheDocument()
  })
})
