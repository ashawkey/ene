import { store } from './storage'

/** Side panels whose width the user can drag. */
export type Panel = 'sidebar' | 'inspector'

interface PanelSpec {
  /** CSS custom property holding the preferred width. */
  cssVar: string
  storageKey: string
  min: number
  /** Absolute cap in pixels. */
  max: number
  /** Cap as a fraction of the viewport width. */
  maxFraction: number
}

// Keep in sync with the clamp() limits in styles.css.
export const PANELS: Record<Panel, PanelSpec> = {
  sidebar: { cssVar: '--sidebar-w', storageKey: 'ene.sidebar-w', min: 220, max: 420, maxFraction: 1 },
  inspector: { cssVar: '--inspector-w', storageKey: 'ene.inspector-w', min: 320, max: Infinity, maxFraction: 0.5 },
}

/** Narrowest the conversation column may get while panels are docked. */
export const MAIN_MIN = 480

export const RESIZE_STEP = 16

/**
 * Width bounds for a panel being resized. `width` and `mainWidth` are the
 * panel's and the conversation column's current rendered widths: the panel can
 * grow only by what the conversation has above its minimum.
 */
export function panelBounds(panel: Panel, width: number, mainWidth: number, viewport: number) {
  const spec = PANELS[panel]
  const max = Math.min(spec.max, spec.maxFraction * viewport, width + mainWidth - MAIN_MIN)
  return { min: spec.min, max: Math.max(spec.min, Math.floor(max)) }
}

export function clampWidth(value: number, bounds: { min: number; max: number }) {
  return Math.round(Math.min(bounds.max, Math.max(bounds.min, value)))
}

export function setPanelWidth(panel: Panel, width: number | null) {
  const { cssVar } = PANELS[panel]
  const root = document.documentElement.style
  if (width === null) root.removeProperty(cssVar)
  else root.setProperty(cssVar, `${width}px`)
}

export function savePanelWidth(panel: Panel, width: number | null) {
  store.set(PANELS[panel].storageKey, width === null ? null : String(width))
}

/** Apply widths remembered from earlier visits; CSS clamps them to the window. */
export function applyStoredWidths() {
  for (const panel of Object.keys(PANELS) as Panel[]) {
    const value = Number(store.get(PANELS[panel].storageKey))
    if (Number.isFinite(value) && value > 0) setPanelWidth(panel, Math.max(PANELS[panel].min, Math.round(value)))
  }
}
