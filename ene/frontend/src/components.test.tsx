import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { vi } from 'vitest'

import { ApiError } from './api'
import { ActivityStatus, Composer, ConnectionBanner, NewSessionButton, PromptDialog, SessionSidebar, Thinking, ThemeToggle } from './components'
import { NewSessionDialog } from './NewSessionDialog'
import type { SessionSummary } from './types'

// Controlled composer wrapper: the draft now lives in the parent (App), so the
// test drives it through local state just like the real app does.
function ControlledComposer({ onSend }: { onSend: (text: string) => void }) {
  const [draft, setDraft] = useState('')
  return (
    <Composer
      operationId={null}
      pending={null}
      busy={false}
      draft={draft}
      onDraftChange={setDraft}
      onSend={onSend}
      onWithdraw={() => undefined}
      onCancel={() => undefined}
    />
  )
}

describe('interaction components', () => {
  it('toggles the theme and labels the next choice', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<ThemeToggle theme="dark" onToggle={onToggle} />)
    const button = screen.getByRole('button', { name: 'Switch to light theme' })
    fireEvent.click(button)
    expect(onToggle).toHaveBeenCalled()
    rerender(<ThemeToggle theme="light" onToggle={onToggle} />)
    expect(screen.getByRole('button', { name: 'Switch to dark theme' })).toBeInTheDocument()
  })

  it('shows reconnecting status with an immediate retry', () => {
    const retry = vi.fn()
    render(<ConnectionBanner status="reconnecting" onRetry={retry} />)
    expect(screen.getByText('Connection lost. Reconnecting…')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry now' }))
    expect(retry).toHaveBeenCalled()
  })

  it('sends composer text with Enter', () => {
    const onSend = vi.fn()
    render(<ControlledComposer onSend={onSend} />)
    const field = screen.getByPlaceholderText('Type Anything...')
    fireEvent.change(field, { target: { value: 'hello' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('hello')
  })

  it('completes slash commands with keyboard navigation', () => {
    const onDraftChange = vi.fn()
    const { rerender } = render(
      <Composer
        operationId={null}
        pending={null}
        busy={false}
        draft="/"
        commands={{ help: 'Show help', context: 'List context' }}
        onDraftChange={onDraftChange}
        onSend={() => undefined}
        onWithdraw={() => undefined}
        onCancel={() => undefined}
      />,
    )
    const field = screen.getByPlaceholderText('Type Anything...')
    expect(screen.getByRole('option', { name: '/help Show help' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(field, { key: 'ArrowDown' })
    expect(screen.getByRole('option', { name: '/context List context' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(field, { key: 'Tab' })
    expect(onDraftChange).toHaveBeenLastCalledWith('/context')

    rerender(
      <Composer
        operationId={null}
        pending={null}
        busy={false}
        draft="/hel"
        commands={{ help: 'Show help', context: 'List context' }}
        onDraftChange={onDraftChange}
        onSend={() => undefined}
        onWithdraw={() => undefined}
        onCancel={() => undefined}
      />,
    )
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onDraftChange).toHaveBeenLastCalledWith('/help')
  })

  it('submits an exact slash command instead of reopening completion', () => {
    const onSend = vi.fn()
    render(
      <Composer
        operationId={null}
        pending={null}
        busy={false}
        draft="/help"
        commands={{ help: 'Show help' }}
        onDraftChange={() => undefined}
        onSend={onSend}
        onWithdraw={() => undefined}
        onCancel={() => undefined}
      />,
    )
    fireEvent.keyDown(screen.getByPlaceholderText('Type Anything...'), { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('/help')
  })

  it('keeps drafts editable but disables actions while disconnected', () => {
    render(
      <Composer
        operationId="op"
        pending={null}
        busy={false}
        connected={false}
        draft="unsent text"
        onDraftChange={() => undefined}
        onSend={() => undefined}
        onWithdraw={() => undefined}
        onCancel={() => undefined}
      />,
    )
    expect(screen.getByPlaceholderText('Queue a message...')).not.toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
  })

  it('shows pending input and offers withdrawal', () => {
    const onWithdraw = vi.fn()
    render(
      <Composer
        operationId="op"
        pending={{ id: 'p', text: 'follow up later', source: 'terminal', action_id: null }}
        busy={false}
        draft=""
        onDraftChange={() => undefined}
        onSend={() => undefined}
        onWithdraw={onWithdraw}
        onCancel={() => undefined}
      />,
    )
    expect(screen.getByText('follow up later')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Queue a message...')).not.toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /Pending/ }))
    expect(onWithdraw).toHaveBeenCalled()
  })

  it('highlights the command in a permission prompt', () => {
    const { container } = render(
      <PromptDialog
        prompt={{
          id: '3',
          kind: 'select',
          message: 'Allow this call?\nexec_command: ls -la',
          choices: ['Yes', 'No'],
          default: 'Yes',
        }}
        onAnswer={() => undefined}
      />,
    )
    expect(screen.getByText('Allow this call?')).toBeInTheDocument()
    expect(container.querySelector('.cmd-label')).toHaveTextContent('exec_command')
    expect(container.querySelector('.command .cmd-name')).toHaveTextContent('ls')
    expect(container.querySelector('.command .cmd-flag')).toHaveTextContent('-la')
  })

  it('counts elapsed seconds while working', () => {
    vi.useFakeTimers()
    try {
      render(<Thinking />)
      expect(screen.getByText('Working')).toBeInTheDocument()
      expect(screen.getByText('0s')).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(2000) })
      expect(screen.getByText('Working')).toBeInTheDocument()
      expect(screen.getByText('2s')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows frozen accumulated round time after status details', () => {
    vi.useFakeTimers()
    try {
      render(
        <Thinking
          contextTokens={13}
          contextLimit={100}
          inputTokens={226_000}
          outputTokens={5_000}
          cachedTokens={180_800}
          roundElapsed={312}
        />,
      )
      expect(screen.getByText('Working')).toBeInTheDocument()
      expect(screen.getByText('0s')).toBeInTheDocument()
      expect(screen.getByText('↑226K · ↓5K · 80% hit')).toBeInTheDocument()
      expect(screen.getByText('· 5m')).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(2000) })
      expect(screen.getByText('Working')).toBeInTheDocument()
      expect(screen.getByText('2s')).toBeInTheDocument()
      expect(screen.getByText('· 5m')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('counts down while waiting', () => {
    vi.useFakeTimers()
    try {
      render(<Thinking label="Waiting" countdown={61} />)
      expect(screen.getByText('Waiting')).toBeInTheDocument()
      expect(screen.getByText('1m 01s')).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(2000) })
      expect(screen.getByText('59s')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('falls back to working while an operation has no specific indicator', () => {
    render(<ActivityStatus busy status={null} />)
    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()
  })

  it('keeps the specific activity while an operation is busy', () => {
    render(<ActivityStatus busy status={{ label: 'Executing', suffix: 'read_file' }} />)
    expect(screen.getByText('Executing')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()
    expect(screen.getByText('read_file')).toBeInTheDocument()
  })

  it('binds session activity to the composer shell', () => {
    const { container } = render(
      <Composer
        activity={<ActivityStatus busy status={{ label: 'Executing' }} />}
        operationId="op"
        pending={null}
        busy={false}
        draft=""
        onDraftChange={() => undefined}
        onSend={() => undefined}
        onWithdraw={() => undefined}
        onCancel={() => undefined}
      />,
    )
    expect(container.querySelector('.composer-shell > .activity-dock')).toBeInTheDocument()
  })

  it('shows each background process as a separate activity row', () => {
    render(
      <ActivityStatus
        busy={false}
        status={null}
        processStatus={{
          running: 2,
          finished: 1,
          processes: [
            { process_id: '1', label: 'review parser', elapsed_seconds: 12, last_line: 'reading src/parse.py' },
            { process_id: '2', label: 'run tests', elapsed_seconds: 75, last_line: '' },
          ],
        }}
      />,
    )
    const panel = screen.getByLabelText('background processes')
    expect(within(panel).getAllByRole('listitem')).toHaveLength(2)
    expect(within(panel).getByText('#1')).toBeInTheDocument()
    expect(within(panel).getByText(/review parser/)).toBeInTheDocument()
    expect(within(panel).getByText('reading src/parse.py')).toBeInTheDocument()
    expect(within(panel).getByText('1m 15s')).toBeInTheDocument()
    expect(within(panel).getByText('Waiting for output…')).toBeInTheDocument()
    expect(within(panel).queryByText('Background tasks')).not.toBeInTheDocument()
    expect(within(panel).queryByText('1 finished')).not.toBeInTheDocument()
  })

  it('keeps context usage visible while idle', () => {
    render(
      <ActivityStatus
        busy={false}
        status={null}
        contextStatus={{
          contextTokens: 96_000,
          contextLimit: 128_000,
          inputTokens: 200_000,
          outputTokens: 4_000,
          cachedTokens: 150_000,
        }}
      />,
    )
    expect(screen.queryByText(/Working/)).not.toBeInTheDocument()
    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(screen.getByText('↑200K · ↓4K · 75% hit')).toBeInTheDocument()
  })

  it('keeps context usage visible during other activity', () => {
    render(
      <ActivityStatus
        busy
        status={{ label: 'Executing', suffix: 'read_file' }}
        contextStatus={{
          contextTokens: 64_000,
          contextLimit: 128_000,
          inputTokens: 20_000,
          outputTokens: 2_000,
          cachedTokens: 10_000,
        }}
      />,
    )
    expect(screen.getByText('Executing')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('↑20K · ↓2K · 50% hit')).toBeInTheDocument()
    expect(screen.getByLabelText('working').querySelector('.activity-operation-line > .context-usage')).toBeInTheDocument()
  })

  it('hides the status before context usage is available', () => {
    const { container } = render(<ActivityStatus busy={false} status={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('preserves elapsed time when remounted', () => {
    vi.useFakeTimers()
    try {
      const startedAt = Date.now()
      act(() => { vi.advanceTimersByTime(5000) })
      render(<Thinking startedAt={startedAt} />)
      expect(screen.getByText('Working')).toBeInTheDocument()
      expect(screen.getByText('5s')).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows indeterminate compaction progress', () => {
    const { container } = render(
      <Thinking
        label="Compacting"
        progress
        suffix="436 messages, ~305,603 tokens"
      />,
    )
    expect(screen.getByText('Compacting')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()
    expect(screen.getByText('436 messages, ~305,603 tokens')).toBeInTheDocument()
    expect(container.querySelector('.indeterminate-progress > i')).toBeInTheDocument()
  })

  it('shows terminal-style context progress while working', () => {
    const { container } = render(
      <Thinking
        contextTokens={1_000}
        contextLimit={128_000}
        inputTokens={2_000}
        outputTokens={500}
        cachedTokens={1_600}
      />,
    )
    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()
    expect(screen.getByText('1%')).toBeInTheDocument()
    expect(screen.getByText('↑2K · ↓500 · 80% hit')).toBeInTheDocument()
    expect(container.querySelector('.context-progress > i')).toHaveStyle({ width: '0.78125%' })
  })

  it('renders prompt choices as separate buttons', () => {
    const answer = vi.fn()
    render(
      <PromptDialog
        prompt={{ id: '1', kind: 'select', message: 'Allow?', choices: ['Allow', 'Deny'], default: '' }}
        onAnswer={answer}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Allow' }))
    expect(answer).toHaveBeenCalledWith('Allow')
    expect(screen.getByRole('button', { name: 'Deny' }).parentElement).toHaveClass('prompt-options')
  })

  it('marks the default choice as primary regardless of position', () => {
    render(
      <PromptDialog
        prompt={{ id: '2', kind: 'select', message: 'Allow?', choices: ['Allow', 'Deny'], default: 'Deny' }}
        onAnswer={() => undefined}
      />,
    )
    expect(screen.getByRole('button', { name: 'Deny' })).toHaveClass('primary')
    expect(screen.getByRole('button', { name: 'Allow' })).not.toHaveClass('primary')
  })
})

function summary(overrides: Partial<SessionSummary> & { id: string }): SessionSummary {
  return {
    title: overrides.id,
    name: '',
    cwd: `/w/${overrides.id}`,
    model: 'm',
    host: '',
    conversation_id: '',
    preview: '',
    attached_by: '',
    state: 'done',
    ...overrides,
  }
}

describe('SessionSidebar', () => {
  const sidebar = (props: Partial<Parameters<typeof SessionSidebar>[0]> = {}) => (
    <SessionSidebar
      sessions={props.sessions ?? []}
      activeId={props.activeId ?? null}
      busyId={props.busyId ?? null}
      open={props.open ?? false}
      onSelect={props.onSelect ?? (() => undefined)}
      onAttach={props.onAttach ?? (() => undefined)}
      onDetach={props.onDetach ?? (() => undefined)}
      onClose={props.onClose ?? (() => undefined)}
    />
  )

  it('separates attached sessions from detached ones', () => {
    render(sidebar({
      sessions: [
        summary({ id: 'mine', name: 'mine', attached_by: 'web' }),
        summary({ id: 'free', name: 'free' }),
        summary({ id: 'shell', name: 'shell', attached_by: 'terminal' }),
      ],
      activeId: 'mine',
    }))

    expect(screen.getByRole('button', { name: 'mine' })).toHaveClass('active')
    expect(screen.getByRole('button', { name: 'free' })).toHaveClass('detached')
    expect(screen.getByText('Detached')).toBeInTheDocument()
    // Only the hub-owned session offers a detach control.
    expect(screen.getAllByRole('button', { name: /^Detach/ })).toHaveLength(1)
  })

  it('attaches a detached session and refuses a terminal-owned one', () => {
    const attach = vi.fn()
    render(sidebar({
      sessions: [
        summary({ id: 'free', name: 'free' }),
        summary({ id: 'shell', name: 'shell', attached_by: 'terminal' }),
      ],
      onAttach: attach,
    }))

    fireEvent.click(screen.getByRole('button', { name: 'free' }))
    expect(attach).toHaveBeenCalledWith('free')

    const owned = screen.getByRole('button', { name: /shell/ })
    expect(owned).toBeDisabled()
    expect(owned).toHaveAttribute('title', expect.stringContaining('terminal'))
  })

  it('detaches an attached session and disables the control while busy', () => {
    const detach = vi.fn()
    const { rerender } = render(sidebar({
      sessions: [summary({ id: 'mine', name: 'mine', attached_by: 'web' })],
      onDetach: detach,
    }))

    fireEvent.click(screen.getByRole('button', { name: 'Detach mine' }))
    expect(detach).toHaveBeenCalledWith('mine')

    rerender(sidebar({
      sessions: [summary({ id: 'mine', name: 'mine', attached_by: 'web' })],
      busyId: 'mine',
      onDetach: detach,
    }))
    expect(screen.getByRole('button', { name: 'Detach mine' })).toBeDisabled()
  })

  it('opens the new-session dialog from the top bar', () => {
    const onNew = vi.fn()
    render(<NewSessionButton onClick={onNew} />)
    fireEvent.click(screen.getByRole('button', { name: 'New session' }))
    expect(onNew).toHaveBeenCalled()
  })

  it('labels a card by name, else the last message, else "New session"', () => {
    render(sidebar({
      sessions: [
        summary({ id: 'a', name: 'named', preview: 'ignored', attached_by: 'web' }),
        summary({ id: 'b', preview: 'refactor the parser', cwd: '/home/dev/workspace-a', attached_by: 'web' }),
        summary({ id: 'c', cwd: '/home/dev/fresh', attached_by: 'web' }),
      ],
    }))

    expect(screen.getByText('named')).toBeInTheDocument()
    expect(screen.getByText('refactor the parser')).toBeInTheDocument()
    // A session with no name and no messages yet must not repeat its
    // directory, which is already the second line.
    expect(screen.getByText('New session')).toHaveClass('session-name')
    expect(screen.getByText('fresh')).toHaveClass('session-meta')
    // The second line is always the working directory's own name.
    expect(screen.getByText('workspace-a')).toHaveClass('session-meta')
    // A long preview must not bloat the detach control's accessible name: an
    // unnamed session falls back to its directory there.
    expect(screen.getByRole('button', { name: 'Detach workspace-a' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Detach fresh' })).toBeInTheDocument()
  })

  it('closes the drawer when a session is chosen', () => {
    const onSelect = vi.fn()
    const onClose = vi.fn()
    render(sidebar({
      sessions: [summary({ id: 'mine', name: 'mine', attached_by: 'web' })],
      open: true,
      onSelect,
      onClose,
    }))

    expect(document.querySelector('.sidebar')).toHaveClass('open')
    fireEvent.click(screen.getByRole('button', { name: 'mine' }))
    expect(onSelect).toHaveBeenCalledWith('mine')

    // Tapping the scrim dismisses the drawer without selecting anything.
    fireEvent.click(document.querySelector('.sidebar-scrim') as HTMLElement)
    expect(onClose).toHaveBeenCalled()
  })
})

describe('NewSessionDialog', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.startsWith('/api/fs')
        ? {
            path: url.includes('path=') ? '/home/dev/proj' : '/home/dev',
            parent: '/home',
            entries: [
              { name: 'proj', path: '/home/dev/proj', hidden: false },
              { name: '.cache', path: '/home/dev/.cache', hidden: true },
            ],
          }
        : url.startsWith('/api/workspaces')
          ? { workspaces: ['/home/dev/proj'] }
          : url.startsWith('/api/options')
            ? { models: ['gpt', 'fast'], default_model: 'gpt', personas: ['coder'], reasoning_efforts: ['high'] }
            : url.startsWith('/api/conversations')
              ? { conversations: [{ id: 'c1', name: 'past', message_count: 4, round_id: 1, last_user_message: 'hi', live: false }] }
              : {}
      return { ok: true, status: 200, statusText: 'OK', json: async () => body } as Response
    }))
  })

  afterEach(() => vi.unstubAllGlobals())

  const browserEntries = () =>
    within(document.querySelector('.browser-list') as HTMLElement).queryAllByRole('button')

  it('browses directories and hides dotted entries by default', async () => {
    render(<NewSessionDialog onCreate={async () => undefined} onClose={() => undefined} />)

    await waitFor(() => expect(browserEntries()).not.toHaveLength(0))
    expect(browserEntries().map((item) => item.textContent)).toEqual(['proj'])

    fireEvent.click(screen.getByLabelText('Show hidden'))
    expect(browserEntries().map((item) => item.textContent)).toEqual(['proj', '.cache'])
  })

  it('submits the selected workspace and options', async () => {
    const create = vi.fn(async () => undefined)
    render(<NewSessionDialog onCreate={create} onClose={() => undefined} />)

    await waitFor(() => expect(browserEntries()).not.toHaveLength(0))
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'review' } })
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Resume conversation'), { target: { value: 'c1' } })
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    })

    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      cwd: '/home/dev',
      name: 'review',
      model: 'gpt',
      persona: 'coder',
      reasoning_effort: 'high',
      resume: 'c1',
    }))
  })

  it('shows the hub error when creation fails', async () => {
    const create = vi.fn(async () => {
      throw new ApiError("A live session named 'dup' already exists", 400)
    })
    render(<NewSessionDialog onCreate={create} onClose={() => undefined} />)

    await waitFor(() => expect(browserEntries()).not.toHaveLength(0))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    })

    expect(screen.getByRole('alert')).toHaveTextContent('already exists')
    expect(screen.getByRole('button', { name: 'Start session' })).toBeEnabled()
  })
})


describe('session status indicators', () => {
  const dot = (id: string) =>
    document.querySelector(`[data-session="${id}"] .session-state`) as HTMLElement

  it('distinguishes working, waiting, done, and terminal-owned sessions', () => {
    render(
      <SessionSidebar
        sessions={[
          summary({ id: 'w', name: 'w', attached_by: 'web', state: 'working' }),
          summary({ id: 'q', name: 'q', attached_by: 'web', state: 'waiting' }),
          summary({ id: 'd', name: 'd', attached_by: 'web', state: 'done' }),
          summary({ id: 'r', name: 'r', attached_by: '', state: 'done' }),
          summary({ id: 't', name: 't', attached_by: 'terminal', state: 'done' }),
        ]}
        activeId={null}
        busyId={null}
        open={false}
        onSelect={() => undefined}
        onAttach={() => undefined}
        onDetach={() => undefined}
        onClose={() => undefined}
      />,
    )

    // Working animates like the terminal spinner: three bouncing dots. The
    // class is `busy` so the global .working timeline rule cannot override it.
    expect(dot('w')).toHaveClass('busy')
    expect(dot('w')).not.toHaveClass('working')
    expect(dot('w').querySelectorAll('i')).toHaveLength(3)

    expect(dot('q')).toHaveClass('waiting')
    // Finished work is green while attached and yellow while detached.
    expect(dot('d')).toHaveClass('done')
    expect(dot('r')).toHaveClass('review')
    expect(dot('r')).not.toHaveClass('done')
    // Terminal ownership replaces the state dot rather than adding a badge.
    expect(dot('t')).toHaveClass('terminal')
    expect(screen.queryByText('terminal')).not.toBeInTheDocument()

    // Dots are decorative; the status stays discoverable via the row tooltip
    // without padding every tab's accessible name.
    expect(screen.getByRole('button', { name: 'w' })).toHaveAttribute(
      'title', expect.stringContaining('Working'),
    )
    expect(screen.getByRole('button', { name: 't' })).toHaveAttribute(
      'title', expect.stringContaining('Attached in a terminal'),
    )
  })
})
