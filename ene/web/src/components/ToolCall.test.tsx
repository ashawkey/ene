import { fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { TimelineItem } from '../lib/session'
import { ToolCall } from './TimelineItems'

type ToolItem = Extract<TimelineItem, { kind: 'tool' }>

const command = 'python -m pytest tests/test_backend.py tests/test_context.py -q -k "long and expression"'

function tool(): ToolItem {
  return {
    kind: 'tool', key: 't', seq: 1, ts: null, name: 'exec_command', primary: command,
    qualifiers: [], text: '', result: { text: '', success: true, image: null }, output: null, diffs: [],
  } as unknown as ToolItem
}

function stubWidths(scroll: number, client: number) {
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(scroll)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(client)
}

afterEach(() => vi.restoreAllMocks())

describe('tool call arguments', () => {
  it('shows a clipped command in full in the body', () => {
    stubWidths(800, 200)
    const { container } = render(<ToolCall item={tool()} live={false} />)
    expect(container.querySelector('.tool-full')?.textContent).toBe(command)
    fireEvent.click(container.querySelector('.step-head')!)
    expect(container.querySelector('.tool-full')).toBeNull()
  })

  it('stays a plain row when the command fits', () => {
    stubWidths(200, 200)
    const { container } = render(<ToolCall item={tool()} live={false} />)
    expect(container.querySelector('.tool-full')).toBeNull()
    expect(container.querySelector('button.step-head')).toBeNull()
  })
})

describe('tool call output', () => {
  it('says how many earlier lines were dropped from long output', () => {
    stubWidths(200, 200)
    const item = { ...tool(), output: { text: 'last line', ansi: null, trimmed: 12345 } }
    const { container } = render(<ToolCall item={item} live />)
    expect(container.textContent).toContain('12,345 earlier lines not shown')
  })
})
