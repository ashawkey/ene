import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

vi.mock('./lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./lib/api')>()),
  sessionOptions: vi.fn(async () => ({ models: [], default_model: '', personas: [], reasoning_efforts: [] })),
  gitBranch: vi.fn(async () => ({ git: false, branch: '', detached: false })),
}))

class FakeSocket {
  static all: FakeSocket[] = []
  static OPEN = 1
  static CONNECTING = 0
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null

  constructor(public url: string) {
    FakeSocket.all.push(this)
  }

  send() {}

  close() {
    this.readyState = 3
  }

  open() {
    this.readyState = 1
    this.onopen?.()
  }

  push(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
}

const control = () => FakeSocket.all.filter((socket) => !socket.url.includes('session=')).at(-1)!

const session = (state: string) => ({
  id: 's1', title: 'one', name: 'one', cwd: '/work', model: 'm', host: '', conversation_id: 'c',
  preview: '', attached_by: 'web', state, watchers: 0, background: 0,
})

let visibility: DocumentVisibilityState = 'visible'

function signIn(state = 'working') {
  render(<App />)
  act(() => {
    control().open()
    control().push({ type: 'sessions', csrf: 'c', host: 'box', sessions: [session(state)] })
  })
}

beforeEach(() => {
  FakeSocket.all = []
  visibility = 'visible'
  vi.stubGlobal('WebSocket', FakeSocket)
  HTMLElement.prototype.scrollTo = () => {}
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
  window.localStorage.clear()
  window.localStorage.setItem('ene.sound', 'off')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('App', () => {
  it('marks the session in view read when the tab is shown again', () => {
    signIn('working')
    visibility = 'hidden'
    act(() => control().push({ type: 'sessions', sessions: [session('done')] }))
    expect(document.title).toMatch(/^\(1\) /)
    visibility = 'visible'
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(document.title).not.toMatch(/^\(1\) /)
  })

  it('leaves Ctrl+letter text editing alone on macOS and uses Cmd', () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel')
    signIn()
    const search = screen.getByPlaceholderText(/Search sessions/)
    // Ctrl+K deletes to the end of the line in macOS text fields.
    expect(fireEvent.keyDown(search, { key: 'k', ctrlKey: true })).toBe(true)
    expect(fireEvent.keyDown(search, { key: 'b', ctrlKey: true })).toBe(true)
    expect(fireEvent.keyDown(search, { key: 'b', metaKey: true })).toBe(false)
  })

  it('uses Ctrl elsewhere', () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('Linux x86_64')
    signIn()
    const search = screen.getByPlaceholderText(/Search sessions/)
    expect(fireEvent.keyDown(search, { key: 'b', ctrlKey: true })).toBe(false)
  })

  it('explains a refused page origin instead of looping through sign-in', () => {
    render(<App />)
    act(() => {
      control().readyState = 3
      control().onclose?.({ code: 4400 })
    })
    expect(screen.getByRole('alert').textContent).toMatch(/Host header/)
    expect(screen.queryByPlaceholderText('Token')).toBeNull()
  })
})
