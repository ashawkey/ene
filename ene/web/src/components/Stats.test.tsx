import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { RoundStats } from '../lib/protocol'
import { StatsPanel } from './Stats'

// The panel reads the clock once per render: count renders through it.
const renders = vi.hoisted(() => ({ count: 0 }))
vi.mock('./ui', async (importOriginal) => {
  const ui = await importOriginal<typeof import('./ui')>()
  return { ...ui, useNow: (active: boolean) => { renders.count++; return ui.useNow(active) } }
})

const round = (index: number): RoundStats => ({
  id: `r${index}`, index, kind: 'user', model: 'fast', prompt: `task ${index}`, started_at: 0, ended_at: 2, duration: 2,
  start_seq: 0, input_tokens: 100, output_tokens: 10, cached_tokens: 50, reasoning_tokens: 0, context_tokens: 100,
  context_limit: 1000, tool_calls: 1, tool_failures: 0, edits: 0, cancelled: false, model_calls: 1, model_time: 1,
  tool_time: 0.5, ttft: 0.3, tps: 40,
})

describe('statistics panel', () => {
  it('does not re-render for a streamed frame that changed none of its props', () => {
    const rounds = [round(1), round(2)]
    const onJump = vi.fn()
    const panel = (list: RoundStats[]) => (
      <StatsPanel rounds={list} active={false} context={null} operationSince={null} onJump={onJump} />
    )
    const { rerender } = render(panel(rounds))
    const initial = renders.count
    rerender(panel(rounds))
    expect(renders.count).toBe(initial)
    rerender(panel([...rounds, round(3)]))
    expect(renders.count).toBe(initial + 1)
    expect(screen.getByText('task 3')).toBeInTheDocument()
  })
})
