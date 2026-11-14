import type { KeyboardEvent, PointerEvent } from 'react'
import { useRef, useState } from 'react'

import type { Panel } from '../lib/resize'
import { PANELS, RESIZE_STEP, clampWidth, panelBounds, savePanelWidth, setPanelWidth } from '../lib/resize'

const PANEL_SELECTOR: Record<Panel, string> = {
  sidebar: '.sidebar',
  inspector: '.pane.active .inspector',
}

function measure(panel: Panel) {
  const element = document.querySelector<HTMLElement>(PANEL_SELECTOR[panel])
  const main = document.querySelector<HTMLElement>('.pane.active .pane-main') ?? document.querySelector<HTMLElement>('.workspace')
  if (!element || !main) return null
  const width = element.getBoundingClientRect().width
  return { width, bounds: panelBounds(panel, width, main.getBoundingClientRect().width, window.innerWidth) }
}

/**
 * Drag handle in the gap beside a side panel. Dragging writes the width to a
 * CSS variable (no React render per move); the result is remembered on release.
 * Double-click restores the default width.
 */
export function ResizeHandle({ panel }: { panel: Panel }) {
  // The sidebar grows to the right, the inspector to the left.
  const direction = panel === 'sidebar' ? 1 : -1
  const drag = useRef<{ x: number; width: number; bounds: { min: number; max: number }; next: number; frame: number; moved: boolean } | null>(null)
  const [value, setValue] = useState<number | null>(null)

  const finish = (width: number) => {
    setPanelWidth(panel, width)
    savePanelWidth(panel, width)
    setValue(width)
  }

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const current = measure(panel)
    if (!current) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    drag.current = { x: event.clientX, width: current.width, bounds: current.bounds, next: current.width, frame: 0, moved: false }
    document.documentElement.classList.add('resizing')
  }

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const state = drag.current
    if (!state) return
    if (event.clientX !== state.x) state.moved = true
    state.next = clampWidth(state.width + direction * (event.clientX - state.x), state.bounds)
    if (!state.frame) {
      state.frame = requestAnimationFrame(() => {
        state.frame = 0
        setPanelWidth(panel, state.next)
      })
    }
  }

  const onPointerEnd = () => {
    const state = drag.current
    if (!state) return
    drag.current = null
    if (state.frame) cancelAnimationFrame(state.frame)
    document.documentElement.classList.remove('resizing')
    // A click is no resize: saving the measured width would pin one that CSS
    // shrank to fit the window.
    if (state.moved) finish(state.next)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const delta = event.key === 'ArrowRight' ? RESIZE_STEP : event.key === 'ArrowLeft' ? -RESIZE_STEP : 0
    if (!delta) return
    const current = measure(panel)
    if (!current) return
    event.preventDefault()
    finish(clampWidth(current.width + direction * delta, current.bounds))
  }

  const reset = () => {
    setPanelWidth(panel, null)
    savePanelWidth(panel, null)
    setValue(null)
  }

  const label = panel === 'sidebar' ? 'Resize sidebar' : 'Resize panel'
  return (
    <div
      className={`resize-handle resize-${panel}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={PANELS[panel].min}
      aria-valuenow={value ?? undefined}
      tabIndex={0}
      title={`${label} (double-click to reset)`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onLostPointerCapture={onPointerEnd}
      onDoubleClick={reset}
      onKeyDown={onKeyDown}
    />
  )
}
