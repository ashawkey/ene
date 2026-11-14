import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { stopProcess } from '../lib/api'
import type { SessionSummary } from '../lib/protocol'
import type { LiveProcesses } from '../lib/session'
import { Inspector } from './Inspector'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  stopProcess: vi.fn(),
}))
vi.mock('./Terminal', () => ({ TerminalPanel: () => null }))

const processes: LiveProcesses = {
  running: 1,
  finished: 0,
  receivedAt: Date.now(),
  processes: [{ process_id: 'p1', label: 'npm run dev', elapsed_seconds: 5, last_line: '', status: 'running' }],
}

const renderTasks = () => render(
  <Inspector
    tab="tasks"
    onTab={() => {}}
    onClose={() => {}}
    sessionId="s1"
    summary={{ id: 's1', cwd: '/work' } as SessionSummary}
    rounds={[]}
    busy={false}
    context={null}
    operationSince={null}
    processes={processes}
    info={{}}
    connected
    filesRefresh={0}
    onOpenFile={() => {}}
    onMention={() => {}}
    onJumpRound={() => {}}
    onOpenProcess={() => {}}
  />,
)

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(stopProcess).mockResolvedValue({ ok: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('processes panel', () => {
  it('keeps a stop button armed again soon after for the full delay', () => {
    renderTasks()
    const stop = () => screen.getByRole('button', { name: /stop/i })
    fireEvent.click(stop())
    act(() => { vi.advanceTimersByTime(1000) })
    fireEvent.click(stop())
    expect(stopProcess).toHaveBeenCalledTimes(1)
    // The process kept running; arm again 2 s after the first arm.
    act(() => { vi.advanceTimersByTime(1000) })
    fireEvent.click(stop())
    act(() => { vi.advanceTimersByTime(1500) })
    expect(stop()).toHaveAccessibleName('Click again to stop')
    fireEvent.click(stop())
    expect(stopProcess).toHaveBeenCalledTimes(2)
  })
})
