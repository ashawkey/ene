import { fireEvent, render } from '@testing-library/react'
import { createRef } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionView, TimelineItem } from '../lib/session'
import { initialView } from '../lib/session'
import type { TimelineHandle } from './Timeline'
import { Timeline } from './Timeline'

const CLIENT = 400

// jsdom has no layout: give the scroller a controllable geometry.
let contentHeight = 1000
let top = 0
const clamp = (value: number) => Math.max(0, Math.min(value, contentHeight - CLIENT))

beforeEach(() => {
  contentHeight = 1000
  top = 0
  vi.stubGlobal('ResizeObserver', undefined)
  HTMLElement.prototype.scrollTo = () => {}
})

function geometry(element: HTMLElement) {
  Object.defineProperty(element, 'scrollHeight', { configurable: true, get: () => contentHeight })
  Object.defineProperty(element, 'clientHeight', { configurable: true, get: () => CLIENT })
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    get: () => top,
    set: (value: number) => { top = clamp(value) },
  })
  element.scrollTo = ((options: ScrollToOptions) => { top = clamp(options.top ?? top) }) as typeof element.scrollTo
}

const message = (n: number): TimelineItem =>
  ({ kind: 'assistant', key: `a${n}`, seq: n, ts: 0, text: `reply ${n}`, streaming: false })

const viewWith = (count: number): SessionView =>
  ({ ...initialView(), items: Array.from({ length: count }, (_, n) => message(n + 1)) })

function setup() {
  const props = {
    active: true,
    handle: createRef<TimelineHandle>(),
    query: '',
    matchIndex: 0,
    onMatches: () => {},
    onOpenFile: () => {},
    onOpenStats: () => {},
  }
  // Mount empty so the geometry is in place before the first follow.
  const result = render(<Timeline view={viewWith(0)} {...props} />)
  const scroller = result.container.querySelector<HTMLElement>('.timeline-scroller')!
  geometry(scroller)
  const show = (count: number) => result.rerender(<Timeline view={viewWith(count)} {...props} />)
  const scrollTo = (value: number) => {
    top = clamp(value)
    fireEvent.scroll(scroller)
  }
  return { show, scrollTo }
}

describe('Timeline tail following', () => {
  it('follows new output while at the bottom', () => {
    const { show } = setup()
    show(1)
    expect(top).toBe(600)
    contentHeight = 1200
    show(2)
    expect(top).toBe(800)
  })

  it('releases the tail on any small upward step', () => {
    const { show, scrollTo } = setup()
    show(1)
    // A few pixels up, well within the old tolerance band.
    scrollTo(595)
    contentHeight = 1200
    show(2)
    expect(top).toBe(595)
    // Further small steps keep working instead of snapping back.
    scrollTo(580)
    contentHeight = 1400
    show(3)
    expect(top).toBe(580)
  })

  it('re-pins once the user returns to the bottom', () => {
    const { show, scrollTo } = setup()
    show(1)
    scrollTo(300)
    scrollTo(600)
    contentHeight = 1200
    show(2)
    expect(top).toBe(800)
  })

  it('stays pinned when shrinking content clamps the position', () => {
    const { show, scrollTo } = setup()
    show(1)
    contentHeight = 900
    scrollTo(top)
    expect(top).toBe(500)
    contentHeight = 1100
    show(2)
    expect(top).toBe(700)
  })
})

describe('Timeline history', () => {
  const props = {
    active: true,
    handle: createRef<TimelineHandle>(),
    query: '',
    matchIndex: 0,
    onMatches: () => {},
    onOpenFile: () => {},
    onOpenStats: () => {},
    onLoadOlder: () => {},
  }
  const order = (container: HTMLElement) =>
    [...container.querySelectorAll('.timeline > *')].map((node) =>
      (node as HTMLElement).dataset.key ?? (node.textContent?.includes('no longer retained') ? 'marker' : 'other'))

  it('marks where condensed history meets full detail', () => {
    const view = { ...viewWith(4), detailFrom: 3 }
    const { container } = render(<Timeline view={view} {...props} />)
    expect(order(container)).toEqual(['a1', 'a2', 'marker', 'a3', 'a4'])
  })

  it('waits for the condensed part before marking the boundary', () => {
    const view = { ...viewWith(2), detailFrom: 1, historyBefore: 1 }
    const { container } = render(<Timeline view={view} {...props} />)
    expect(order(container)).toEqual(['other', 'a1', 'a2'])
    expect(container.textContent).toContain('Loading earlier history')
  })
})
