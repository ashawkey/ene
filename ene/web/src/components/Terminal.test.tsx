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

const xterm = vi.hoisted(() => ({
  instances: [] as Array<{
    emit: (data: string) => void
    osc: (payload: string) => void
  }>,
}))

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80
    rows = 24
    options = {}
    unicode = { activeVersion: '6' }
    private dataHandler = (_data: string) => {}
    private oscHandler = (_payload: string) => true
    parser = {
      registerOscHandler: (_id: number, handler: (payload: string) => boolean) => {
        this.oscHandler = handler
        return { dispose() {} }
      },
    }
    constructor() {
      xterm.instances.push({ emit: (data) => this.dataHandler(data), osc: (payload) => this.oscHandler(payload) })
    }
    loadAddon() {}
    open() {}
    write(_data: unknown, callback?: () => void) { callback?.() }
    reset() {}
    focus() {}
    dispose() {}
    onData(handler: (data: string) => void) {
      this.dataHandler = handler
      return { dispose() {} }
    }
    onBinary() { return { dispose() {} } }
    attachCustomKeyEventHandler() {}
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit() {} } }))
vi.mock('@xterm/addon-unicode11', () => ({ Unicode11Addon: class {} }))
vi.mock('@xterm/addon-web-links', () => ({ WebLinksAddon: class {} }))
vi.mock('@xterm/addon-webgl', () => ({ WebglAddon: class { onContextLoss() {} dispose() {} } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

import { fitTerminal, TerminalPanel } from './Terminal'

const info = (id: string, extra = {}) => ({ id, title: `Terminal ${id}`, shell: 'bash', alive: true, exit_code: null, created: 0, ...extra })

beforeEach(() => {
  vi.stubGlobal('WebSocket', class { close() {} send() {} })
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  api.closeTerminal.mockResolvedValue({ ok: true })
})

afterEach(() => {
  xterm.instances.length = 0
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
    expect(api.createTerminal).toHaveBeenCalledWith('s1', { cols: 80, rows: 24 }, undefined)
    expect(await screen.findByRole('tab', { name: 'Terminal 1' })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens a terminal in a requested folder once', async () => {
    api.listTerminals.mockResolvedValue({ supported: true, terminals: [] })
    api.createTerminal.mockResolvedValue(info('1'))
    const onOpened = vi.fn()
    const request = { path: 'src/pkg', id: 1 }
    const { rerender } = render(<TerminalPanel sessionId="s1" connected openAt={request} onOpened={onOpened} />)
    expect(await screen.findByRole('tab', { name: 'Terminal 1' })).toHaveAttribute('aria-selected', 'true')
    rerender(<TerminalPanel sessionId="s1" connected openAt={{ ...request }} onOpened={onOpened} />)
    await act(async () => {})
    expect(api.createTerminal).toHaveBeenCalledExactlyOnceWith('s1', { cols: 80, rows: 24 }, 'src/pkg')
    expect(onOpened).toHaveBeenCalledOnce()
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

  it('keeps every terminal connected and switches by showing it', async () => {
    const sockets: string[] = []
    vi.stubGlobal('WebSocket', class {
      constructor(url: string) { sockets.push(url) }
      close() {}
      send() {}
    })
    api.listTerminals.mockResolvedValue({ supported: true, terminals: [info('1'), info('2')] })
    render(<TerminalPanel sessionId="s1" connected />)
    await screen.findByRole('tab', { name: 'Terminal 2' })
    const panel = (name: string) =>
      screen.getAllByRole('tabpanel', { hidden: true }).find((item) => item.getAttribute('aria-label') === name)
    expect(panel('Terminal 1')).not.toBeVisible()
    expect(panel('Terminal 2')).toBeVisible()
    await act(async () => fireEvent.click(screen.getByRole('tab', { name: 'Terminal 1' })))
    expect(panel('Terminal 1')).toBeVisible()
    expect(panel('Terminal 2')).not.toBeVisible()
    expect(sockets.sort()).toEqual(['ws://hub/s1/1', 'ws://hub/s1/2'])
  })

  it('drops replies and clipboard writes generated while the scrollback replays', async () => {
    const sockets: Array<{ sent: unknown[]; onopen?: () => void; onmessage?: (event: { data: unknown }) => void; readyState: number }> = []
    vi.stubGlobal('WebSocket', Object.assign(class {
      sent: unknown[] = []
      readyState = 0
      constructor() { sockets.push(this as never) }
      close() {}
      send(data: unknown) { this.sent.push(data) }
    }, { OPEN: 1 }))
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    api.listTerminals.mockResolvedValue({ supported: true, terminals: [info('1')] })
    render(<TerminalPanel sessionId="s1" connected />)
    await screen.findByRole('tab', { name: 'Terminal 1' })
    const ws = sockets[0]
    const term = xterm.instances[0]
    act(() => {
      ws.readyState = 1
      ws.onopen?.()
    })
    const copy = `c;${btoa('copied')}`
    term.emit('\x1b[24;1R') // xterm answering a replayed cursor query
    term.osc(copy)
    const input = () => ws.sent.filter((frame) => typeof frame !== 'string').map((frame) => Array.from(frame as Uint8Array))
    expect(input()).toEqual([])
    expect(writeText).not.toHaveBeenCalled()
    act(() => ws.onmessage?.({ data: JSON.stringify({ type: 'replayed' }) }))
    term.emit('x')
    term.osc(copy)
    expect(input()).toEqual([[120]])
    expect(writeText).toHaveBeenCalledExactlyOnceWith('copied')
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

describe('fitting the terminal', () => {
  const setup = (cols: number, proposed: number, buffer = 'normal') => {
    const modes: Array<boolean | undefined> = []
    const term = { cols, buffer: { active: { type: buffer } }, options: {} as { windowsMode?: boolean } }
    const fit = {
      proposeDimensions: () => ({ cols: proposed, rows: 24 }),
      fit: () => modes.push(term.options.windowsMode),
    }
    return { term, fit, modes }
  }
  const run = (s: ReturnType<typeof setup>, running: boolean) =>
    fitTerminal(s.term as never, s.fit as never, running)

  it('clips instead of re-wrapping when a running program narrows', () => {
    const s = setup(80, 60)
    run(s, true)
    expect(s.modes).toEqual([true])
    expect(s.term.options.windowsMode).toBe(false)
  })

  it('re-wraps at a shell prompt, when widening, and on the alternate screen', () => {
    for (const [s, running] of [[setup(80, 60), false], [setup(60, 80), true], [setup(80, 60, 'alternate'), true]] as const) {
      run(s, running)
      expect(s.modes).toEqual([undefined])
    }
  })
})
