import { describe, expect, it } from 'vitest'

import type { ServerEvent, StateFrame } from './protocol'
import type { SessionView, TimelineItem } from './session'
import { initialView, reduce, totals } from './session'

function state(overrides: Partial<StateFrame> = {}): ServerEvent {
  return {
    type: 'state', csrf: 'c', session: 's1', stream_id: 'stream-a', latest_seq: 0, oldest_seq: 0,
    replay_truncated: false, operation_id: null, process_status: '', context_status: null,
    active_indicator: null, commands: { help: 'Help' }, prompt: null, pending: null, ...overrides,
  } as unknown as ServerEvent
}

function run(messages: ServerEvent[], view: SessionView = initialView()) {
  const signals = []
  for (const message of messages) {
    const [next, emitted] = reduce(view, message)
    view = next
    signals.push(...emitted)
  }
  return { view, signals }
}

const ev = (seq: number, type: string, data: Record<string, unknown> = {}): ServerEvent => ({ seq, type, data, timestamp: 1000 + seq })

describe('session reducer', () => {
  it('streams deltas into one item and finalizes it in place', () => {
    const { view } = run([
      state(),
      ev(1, 'thinking_delta', { text: 'hm' }),
      ev(2, 'assistant_delta', { text: 'Hel' }),
      ev(3, 'assistant_delta', { text: 'lo' }),
      ev(4, 'thinking', { text: 'hmm' }),
      ev(5, 'assistant_message', { text: 'Hello' }),
    ])
    expect(view.items.map((item) => [item.kind, 'text' in item ? item.text : ''])).toEqual([
      ['thinking', 'hmm'],
      ['assistant', 'Hello'],
    ])
    expect(view.items.every((item) => !('streaming' in item) || !item.streaming)).toBe(true)
  })

  it('ignores replayed events it already has', () => {
    const first = run([state(), ev(1, 'user_message', { text: 'a', source: 'web' })])
    const again = run([state(), ev(1, 'user_message', { text: 'a', source: 'web' })], first.view)
    expect(again.view.items).toHaveLength(1)
    expect(again.view.history).toEqual(['a'])
  })

  it('resets the timeline when the stream changes', () => {
    const first = run([state(), ev(1, 'system', { text: 'x' })])
    const { view, signals } = run([state({ stream_id: 'stream-b' })], first.view)
    expect(view.items).toEqual([])
    expect(view.lastSeq).toBe(0)
    expect(signals).toContainEqual({ kind: 'state', pendingActionId: null, streamChanged: true })
  })

  it('pairs tool results with their calls', () => {
    const { view } = run([
      state(),
      ev(1, 'tool_start', { name: 'read_file', primary: 'a.py' }),
      ev(2, 'tool_start', { name: 'search', primary: 'x' }),
      ev(3, 'tool_result', { text: 'A', success: true }),
      ev(4, 'tool_result', { text: 'B', success: false }),
    ])
    const tools = view.items.filter((item) => item.kind === 'tool')
    expect(tools.map((tool) => tool.kind === 'tool' && [tool.name, tool.result?.text, tool.result?.success])).toEqual([
      ['read_file', 'A', true],
      ['search', 'B', false],
    ])
  })

  it('upserts round statistics by id', () => {
    const round = { id: 'r1', index: 1, input_tokens: 10, output_tokens: 1, cached_tokens: 5, duration: 2, tool_calls: 1 }
    const { view } = run([
      state({ rounds: [{ id: 'saved', prompt: 'earlier' }] as never }),
      ev(1, 'operation_start', { id: 'op' }),
      ev(2, 'round_stats', round),
      ev(3, 'round_stats', { ...round, input_tokens: 30 }),
    ])
    expect(view.items.filter((item) => item.kind === 'round')).toHaveLength(1)
    expect(view.rounds.map((item) => [item.id, item.start_seq])).toEqual([['saved', 0], ['r1', 1]])
    expect(totals(view.rounds)).toMatchObject({ rounds: 2, input: 30, output: 1, tools: 1 })
    // A rewind replaces the list but keeps anchors learned from the stream.
    const rewound = run([ev(4, 'round_history', { rounds: [{ id: 'r1' }] })], view).view
    expect(rewound.rounds.map((item) => [item.id, item.start_seq])).toEqual([['r1', 1]])
  })

  it('reports acknowledgements, rejections and withdrawals as signals', () => {
    const { signals, view } = run([
      state({ pending: { id: 'p0', text: 'old', source: 'web', action_id: 'a0' } }),
      { type: 'accepted', action_id: 'a1' },
      ev(1, 'pending_set', { id: 'p1', text: 'hi', source: 'web', action_id: 'a2' }),
      ev(2, 'submission_rejected', { action_id: 'a3', error: 'busy' }),
      ev(3, 'pending_cleared', { id: 'p1', reason: 'withdrawn', action_id: 'w1', text: 'hi' }),
      ev(4, 'draft_set', { text: 'restored' }),
    ])
    expect(signals).toEqual([
      { kind: 'state', pendingActionId: 'a0', streamChanged: false },
      { kind: 'ack', actionId: 'a1' },
      { kind: 'ack', actionId: 'a2' },
      { kind: 'reject', actionId: 'a3', error: 'busy' },
      { kind: 'withdrawn', actionId: 'w1', text: 'hi' },
      { kind: 'draft', text: 'restored' },
    ])
    expect(view.pending).toBeNull()
  })

  it('tracks operation, indicator and prompt state', () => {
    const { view } = run([
      state(),
      ev(1, 'operation_start', { id: 'op' }),
      ev(2, 'thinking_start', { label: 'Thinking', context_tokens: 5, context_limit: 10, input_tokens: 1, output_tokens: 1 }),
      ev(3, 'prompt_open', { id: 'q', kind: 'select', message: 'ok?', choices: ['y'], default: 'y' }),
    ])
    expect(view.operationId).toBe('op')
    expect(view.indicator?.label).toBe('Thinking')
    expect(view.context?.context_limit).toBe(10)
    expect(view.prompt?.id).toBe('q')
    const done = run([ev(4, 'prompt_resolved', { id: 'q' }), ev(5, 'operation_end', { id: 'op' })], view).view
    expect(done.prompt).toBeNull()
    expect(done.operationId).toBeNull()
    expect(done.indicator).toBeNull()
  })

  it('prefers the wide form of printed output', () => {
    const { view } = run([state(), ev(1, 'output', { text: 'a...', ansi: 'a...', wide_text: 'abcdef', wide_ansi: 'abcdef' })])
    expect(view.items[0]).toMatchObject({ kind: 'output', text: 'abcdef', ansi: 'abcdef' })
  })

  it('merges consecutive output blocks', () => {
    const { view } = run([state(), ev(1, 'output', { text: 'a' }), ev(2, 'output', { text: 'b', ansi: '\u001b[32mb\u001b[0m' })])
    expect(view.items).toHaveLength(1)
    expect(view.items[0]).toMatchObject({ kind: 'output', text: 'a\nb', ansi: 'a\n\u001b[32mb\u001b[0m' })
  })

  it('attaches output printed during a call to that call', () => {
    const { view } = run([
      state(),
      ev(1, 'tool_start', { name: 'exec_command', primary: 'ls' }),
      ev(2, 'output', { text: 'a' }),
      ev(3, 'output', { text: 'b' }),
      ev(4, 'tool_result', { text: 'exit 0', success: true }),
      ev(5, 'output', { text: 'after' }),
    ])
    expect(view.items).toHaveLength(2)
    expect(view.items[0]).toMatchObject({ kind: 'tool', output: { text: 'a\nb' }, result: { text: 'exit 0' } })
    expect(view.items[1]).toMatchObject({ kind: 'output', text: 'after' })
  })

  it('settles edit calls with their diffs', () => {
    const diff = (seq: number, path: string, success = true) =>
      ev(seq, 'diff', { path, old_text: 'a', new_text: 'b', success })
    const { view } = run([
      state(),
      ev(1, 'tool_start', { name: 'multi_edit', primary: 'x' }),
      diff(2, 'x.py'),
      diff(3, 'y.py', false),
    ])
    expect(view.items).toHaveLength(1)
    expect(view.items[0]).toMatchObject({ kind: 'tool', result: { success: false } })
    const tool = view.items[0] as Extract<TimelineItem, { kind: 'tool' }>
    expect(tool.diffs.map((d) => d.path)).toEqual(['x.py', 'y.py'])
    const orphan = run([state(), diff(1, 'z.py')]).view
    expect(orphan.items[0]).toMatchObject({ kind: 'diff', path: 'z.py' })
  })
})
