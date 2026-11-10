import { describe, expect, it } from 'vitest'

import { MAIN_MIN, PANELS, clampWidth, panelBounds } from './resize'

describe('panelBounds', () => {
  it('caps the inspector at half the viewport', () => {
    expect(panelBounds('inspector', 400, 1200, 2000).max).toBe(1000)
  })

  it('keeps the conversation at its minimum width', () => {
    // 400 + 700 - 480: the panel can take only what the conversation spares.
    expect(panelBounds('inspector', 400, 700, 2000).max).toBe(400 + 700 - MAIN_MIN)
    expect(panelBounds('sidebar', 288, 600, 2000).max).toBe(288 + 600 - MAIN_MIN)
  })

  it('caps the sidebar absolutely', () => {
    expect(panelBounds('sidebar', 288, 1500, 2000).max).toBe(PANELS.sidebar.max)
  })

  it('never reports a maximum below the minimum', () => {
    const bounds = panelBounds('inspector', 320, 400, 1200)
    expect(bounds).toEqual({ min: PANELS.inspector.min, max: PANELS.inspector.min })
  })
})

describe('clampWidth', () => {
  it('clamps and rounds', () => {
    expect(clampWidth(100.4, { min: 220, max: 420 })).toBe(220)
    expect(clampWidth(999, { min: 220, max: 420 })).toBe(420)
    expect(clampWidth(300.6, { min: 220, max: 420 })).toBe(301)
  })
})
