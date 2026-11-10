import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listTerminals: vi.fn(),
  createTerminal: vi.fn(),
  closeTerminal: vi.fn(),
}))

vi.mock('../lib/api', () => ({
  ...api,
  terminalSocketUrl: (session: string, id: string) => `ws://hub/${session}/${id}`,
  errorMessage: (_exc: unknown, fallback: string) => fallback,
}))

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80
    rows = 24
    options = {}
    loadAddon() {}
    open() {}
    write() {}
    reset() {}
    focus() {}
    dispose() {}
    onData() { return { dispose() {} } }
    onBinary() { return { dispose() {} } }
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

import { TerminalPanel } from './Terminal'

const info = (id: string, extra = {}) => ({ id, title: `Terminal ${id}`, shell: 'bash', alive: true, exit_code: null, created: 0, ...extra })

beforeEach(() => {
  vi.stubGlobal('WebSocket', class { close() {} send() {} })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.closeTerminal.mockResolvedValue({ ok: true })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
  localStorage.clear()
})

describe('terminal panel', () => {
  it('offers to open the first terminal and switches to it', async () => {
    api.listTerminals.mockResolvedValue({ supported: true, terminals: [] })
    api.createTerminal.mockResolvedValue(info('1'))
    render(<TerminalPanel sessionId="s1" connected />)
    const open = await screen.findByRole('button', { name: /New terminal/ })
    await act(async () => fireEvent.click(open))
    expect(api.createTerminal).toHaveBeenCalledWith('s1', { cols: 80, rows: 24 })
    expect(await screen.findByRole('tab', { name: 'Terminal 1' })).toHaveAttribute('aria-selected', 'true')
  })

  it('closes a terminal and selects its neighbour', async () => {
    api.listTerminals.mockResolvedValueOnce({ supported: true, terminals: [info('1'), info('2')] })
    api.listTerminals.mockResolvedValue({ supported: true, terminals: [info('1')] })
    render(<TerminalPanel sessionId="s1" connected />)
    await screen.findByRole('tab', { name: 'Terminal 2' })
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Close Terminal 2' })))
    expect(api.closeTerminal).toHaveBeenCalledWith('s1', '2')
    await waitFor(() => expect(screen.queryByRole('tab', { name: 'Terminal 2' })).toBeNull())
    expect(screen.getByRole('tab', { name: 'Terminal 1' })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows how a finished shell exited', async () => {
    api.listTerminals.mockResolvedValue({ supported: true, terminals: [info('1', { alive: false, exit_code: 3 })] })
    render(<TerminalPanel sessionId="s1" connected />)
    expect(await screen.findByText('Exited with code 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Restart/ })).toBeEnabled()
  })

  it('explains an unsupported hub', async () => {
    api.listTerminals.mockResolvedValue({ supported: false, terminals: [] })
    render(<TerminalPanel sessionId="s1" connected />)
    expect(await screen.findByText(/not supported/)).toBeInTheDocument()
  })
})
