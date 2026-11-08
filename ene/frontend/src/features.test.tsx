import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { vi } from 'vitest'

import { copyText } from './clipboard'
import { Composer, PromptDialog, SessionSidebar, sessionMatches } from './components'
import { FindBar, TaskPanel, liveElapsed } from './panels'
import { ActivityGroup, EventCard } from './renderers'
import { findMatches, groupTimeline, summarizeGroup } from './timeline'
import type { DisplayEvent, SessionSummary } from './types'

function event(key: string, type: string, text = '', data: DisplayEvent['data'] = {}): DisplayEvent {
  return { key, type, text, data }
}

function toolRun(prefix: string, names: string[]): DisplayEvent[] {
  return names.flatMap((name, index) => [
    event(`${prefix}-s${index}`, 'tool_start', name, { name, primary: `arg${index}` }),
    event(`${prefix}-r${index}`, 'tool_result', 'ok', { success: index !== 1 }),
  ])
}

const session = (overrides: Partial<SessionSummary>): SessionSummary => ({
  id: 's',
  title: 't',
  name: '',
  cwd: '/work/repo',
  model: 'model-a',
  host: 'host',
  conversation_id: 'c',
  preview: '',
  attached_by: 'web',
  state: 'idle',
  ...overrides,
})

describe('timeline grouping', () => {
  it('folds long tool runs and leaves short runs and messages inline', () => {
    const events = [
      event('u', 'user_message', 'hi'),
      ...toolRun('a', ['read_file', 'read_file', 'bash']),
      event('m', 'assistant_message', 'done'),
      ...toolRun('b', ['grep', 'bash']),
    ]
    const items = groupTimeline(events)
    expect(items.map((item) => item.kind)).toEqual(['event', 'group', 'event', 'event', 'event', 'event', 'event'])
    const group = items[1]
    expect(group.kind === 'group' && group.key).toBe('group-a-s0')
  })

  it('keeps diffs outside groups', () => {
    const events = [...toolRun('a', ['x', 'y']), event('d', 'diff'), ...toolRun('b', ['z'])]
    expect(groupTimeline(events).every((item) => item.kind === 'event')).toBe(true)
  })

  it('summarizes tool counts and failures', () => {
    const summary = summarizeGroup(toolRun('a', ['read_file', 'bash', 'read_file']))
    expect(summary).toEqual({
      calls: 3,
      failed: 1,
      tools: [['read_file', 2], ['bash', 1]],
      latest: 'read_file arg2',
    })
  })

  it('opens while live and folds when the run ends, unless toggled by hand', () => {
    const events = toolRun('a', ['read_file', 'bash', 'grep'])
    const { container, rerender } = render(<ActivityGroup events={events} live />)
    const details = container.querySelector('details') as HTMLDetailsElement
    expect(details.open).toBe(true)
    expect(container.querySelector('summary')).toHaveTextContent('3 tool calls')
    rerender(<ActivityGroup events={events} live={false} />)
    expect(details.open).toBe(false)
    expect(container.querySelector('summary')).toHaveTextContent('read_file, bash, grep')
    expect(container.querySelector('summary')).toHaveTextContent('1 failed')
  })
})

describe('conversation search', () => {
  it('matches message text and tool details case-insensitively', () => {
    const events = [
      event('1', 'user_message', 'Fix the Parser'),
      event('2', 'tool_start', '', { name: 'read_file', primary: 'src/parser.py' }),
      event('3', 'diff', '', { path: 'src/other.py', new_text: 'parser = 1' }),
      event('4', 'assistant_message', 'unrelated'),
    ]
    expect(findMatches(events, 'parser')).toEqual(['1', '2', '3'])
    expect(findMatches(events, '   ')).toEqual([])
  })

  it('steps through matches with Enter and closes with Escape', () => {
    const onStep = vi.fn()
    const onClose = vi.fn()
    render(<FindBar query="foo" count={3} index={1} onQuery={() => undefined} onStep={onStep} onClose={onClose} />)
    const field = screen.getByLabelText('Search this conversation')
    expect(field).toHaveFocus()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    fireEvent.keyDown(field, { key: 'Enter' })
    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true })
    expect(onStep.mock.calls).toEqual([[1], [-1]])
    fireEvent.keyDown(field, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('reports when nothing matches', () => {
    render(<FindBar query="zzz" count={0} index={0} onQuery={() => undefined} onStep={() => undefined} onClose={() => undefined} />)
    expect(screen.getByText('No matches')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next match' })).toBeDisabled()
  })
})

describe('session search', () => {
  const sessions = [
    session({ id: 'a', name: 'parser work', cwd: '/work/alpha' }),
    session({ id: 'b', preview: 'write docs', cwd: '/work/beta' }),
    session({ id: 'c', cwd: '/work/gamma', attached_by: '' }),
  ]

  it('matches name, preview, directory, and model', () => {
    expect(sessionMatches(sessions[0], 'PARSER')).toBe(true)
    expect(sessionMatches(sessions[1], 'docs')).toBe(true)
    expect(sessionMatches(sessions[2], 'gamma')).toBe(true)
    expect(sessionMatches(sessions[2], 'model-a')).toBe(true)
    expect(sessionMatches(sessions[2], 'nope')).toBe(false)
  })

  it('filters the rail and opens the best match with Enter', () => {
    const onSelect = vi.fn()
    const onAttach = vi.fn()
    render(
      <SessionSidebar
        sessions={sessions}
        activeId="a"
        busyId={null}
        open={false}
        onSelect={onSelect}
        onAttach={onAttach}
        onDetach={() => undefined}
        onClose={() => undefined}
      />,
    )
    const field = screen.getByLabelText('Search sessions')
    fireEvent.change(field, { target: { value: 'gamma' } })
    const nav = screen.getByRole('navigation')
    expect(within(nav).queryByText('parser work')).not.toBeInTheDocument()
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onAttach).toHaveBeenCalledWith('c')
    fireEvent.change(field, { target: { value: 'docs' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onSelect).toHaveBeenCalledWith('b')
    fireEvent.change(field, { target: { value: 'missing' } })
    expect(screen.getByText(/No sessions match/)).toBeInTheDocument()
  })
})

describe('task panel', () => {
  const processStatus = {
    running: 1,
    finished: 2,
    receivedAt: 1_000,
    processes: [{ process_id: '7', label: 'dev server', elapsed_seconds: 30, last_line: 'listening on :3000', watching: false }],
  }

  it('keeps counting runtime between status frames', () => {
    expect(liveElapsed(processStatus.processes[0], 1_000, 6_000)).toBe(35)
    expect(liveElapsed(processStatus.processes[0], undefined, 6_000)).toBe(30)
  })

  it('inspects logs and stops a process after confirmation', () => {
    const onInspect = vi.fn()
    const onStop = vi.fn()
    const onListAll = vi.fn()
    render(
      <TaskPanel
        processStatus={processStatus}
        connected
        onClose={() => undefined}
        onInspect={onInspect}
        onStop={onStop}
        onListAll={onListAll}
      />,
    )
    expect(screen.getByText('listening on :3000')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show log' }))
    expect(onInspect).toHaveBeenCalledWith('7')
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm stop' }))
    expect(onStop).toHaveBeenCalledWith('7')
    fireEvent.click(screen.getByRole('button', { name: /including 2 finished/ }))
    expect(onListAll).toHaveBeenCalledOnce()
  })
})

describe('keyboard shortcuts', () => {
  it('stops the running round with Escape, like the terminal', () => {
    const onCancel = vi.fn()
    render(
      <Composer
        operationId="op"
        pending={null}
        busy={false}
        draft=""
        onDraftChange={() => undefined}
        onSend={() => undefined}
        onWithdraw={() => undefined}
        onCancel={onCancel}
      />,
    )
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledOnce()
  })

  it('answers a selection prompt with its number key', () => {
    const onAnswer = vi.fn()
    render(
      <PromptDialog
        prompt={{ id: 'p', kind: 'select', message: 'Run it?', choices: ['Yes', 'No'], default: 'Yes' }}
        onAnswer={onAnswer}
      />,
    )
    fireEvent.keyDown(window, { key: '2' })
    expect(onAnswer).toHaveBeenCalledWith('No')
  })

  it('dismisses a prompt with Escape or Cancel', () => {
    const onCancel = vi.fn()
    render(
      <PromptDialog
        prompt={{ id: 'p', kind: 'select', message: 'Pick a prompt to rewind to', choices: ['1. round 1'], default: '' }}
        onAnswer={() => undefined}
        onCancel={onCancel}
      />,
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(2)
  })

  it('does not repeat numbering or cancel controls the choices already carry', () => {
    const { container } = render(
      <PromptDialog
        prompt={{ id: 'p', kind: 'select', message: 'Choose rewind mode', choices: ['1. Conversation only', '2. Cancel'], default: '' }}
        onAnswer={() => undefined}
        onCancel={() => undefined}
      />,
    )
    expect(container.querySelector('.choice-key')).toBeNull()
    expect(screen.getAllByRole('button', { name: /Cancel/ })).toHaveLength(1)
  })
})

describe('copy controls', () => {
  it('copies finished messages but not streaming ones', () => {
    const { rerender } = render(<EventCard event={event('1', 'assistant_message', 'hello', { streaming: true })} />)
    expect(screen.queryByRole('button', { name: 'Copy message' })).not.toBeInTheDocument()
    rerender(<EventCard event={event('1', 'assistant_message', 'hello')} />)
    expect(screen.getByRole('button', { name: 'Copy message' })).toBeInTheDocument()
  })

  it('adds a copy control to fenced code blocks', () => {
    render(<EventCard event={event('1', 'assistant_message', '```\nls -la\n```')} />)
    expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  })

  it('falls back to the legacy copy command outside secure contexts', async () => {
    const exec = vi.fn(() => true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    let ok = false
    await act(async () => { ok = await copyText('abc') })
    expect(ok).toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
  })
})
