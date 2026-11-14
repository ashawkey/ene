import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ResizeHandle } from './ResizeHandle'

function layout(sidebar: number, main: number, inspector: number) {
  document.body.innerHTML = `
    <aside class="sidebar"></aside>
    <section class="pane active"><div class="pane-main"></div><aside class="inspector"></aside></section>`
  const widths: Record<string, number> = { sidebar, 'pane-main': main, inspector }
  for (const [name, width] of Object.entries(widths)) {
    const element = document.querySelector(`.${name}`) as HTMLElement
    element.getBoundingClientRect = () => ({ width } as DOMRect)
  }
}

// jsdom has no PointerEvent, so pointer events would lose clientX.
class TestPointerEvent extends MouseEvent {
  pointerId: number
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init)
    this.pointerId = init.pointerId ?? 0
  }
}

beforeEach(() => {
  vi.stubGlobal('PointerEvent', TestPointerEvent)
  vi.stubGlobal('innerWidth', 1600)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 0 })
  HTMLElement.prototype.setPointerCapture = () => {}
})

afterEach(() => {
  vi.unstubAllGlobals()
  document.documentElement.removeAttribute('style')
  localStorage.clear()
})

const width = (name: string) => document.documentElement.style.getPropertyValue(name)

describe('ResizeHandle', () => {
  it('widens the inspector when dragged left, up to what the conversation spares', () => {
    layout(288, 700, 400)
    // As in the app: the handle sits in the conversation column, before the panel.
    const { container } = render(<ResizeHandle panel="inspector" />, { container: document.querySelector('.pane-main') as HTMLElement })
    const handle = container.querySelector('.resize-handle')!
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 900, pointerId: 1 })
    expect(width('--inspector-w')).toBe('500px')
    expect(document.documentElement.classList.contains('resizing')).toBe(true)
    // 400 + 700 - 480 = 620 is the most the conversation can give up.
    fireEvent.pointerMove(handle, { clientX: 0, pointerId: 1 })
    fireEvent.pointerUp(handle, { pointerId: 1 })
    expect(width('--inspector-w')).toBe('620px')
    expect(localStorage.getItem('ene.inspector-w')).toBe('620')
    expect(document.documentElement.classList.contains('resizing')).toBe(false)
  })

  it('leaves the width alone after a click without a drag', () => {
    // CSS has shrunk the panel to fit the window; a click must not pin that width.
    layout(288, 700, 400)
    const { container } = render(<ResizeHandle panel="inspector" />, { container: document.querySelector('.pane-main') as HTMLElement })
    const handle = container.querySelector('.resize-handle')!
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 1000, pointerId: 1 })
    expect(width('--inspector-w')).toBe('')
    expect(localStorage.getItem('ene.inspector-w')).toBeNull()
    expect(document.documentElement.classList.contains('resizing')).toBe(false)
  })

  it('resizes the sidebar from the keyboard and resets on double-click', () => {
    layout(288, 900, 400)
    render(<ResizeHandle panel="sidebar" />, { container: document.body.appendChild(document.createElement('div')) })
    const handle = screen.getByRole('separator', { name: 'Resize sidebar' })
    fireEvent.keyDown(handle, { key: 'ArrowRight' })
    expect(width('--sidebar-w')).toBe('304px')
    expect(handle.getAttribute('aria-valuenow')).toBe('304')
    fireEvent.doubleClick(handle)
    expect(width('--sidebar-w')).toBe('')
    expect(localStorage.getItem('ene.sidebar-w')).toBeNull()
  })
})
