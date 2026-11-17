import type { LucideIcon } from 'lucide-react'
import { type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export type MenuEntry =
  | { label: string; icon?: LucideIcon; onSelect: () => void; disabled?: boolean }
  | 'separator'

export type MenuRequest = { x: number; y: number; label: string; items: MenuEntry[] }

/**
 * Where a menu opened by *event* should appear: the pointer for a right click,
 * otherwise (the context-menu key, a "more" button) below the element.
 */
export function menuPoint(event: ReactMouseEvent<HTMLElement>): { x: number; y: number } {
  if (event.type === 'contextmenu' && (event.clientX || event.clientY)) return { x: event.clientX, y: event.clientY }
  const rect = event.currentTarget.getBoundingClientRect()
  return { x: rect.left, y: rect.bottom + 2 }
}

/** State for one context menu: `open(event, label, items)` shows it, `close()` hides it. */
export function useContextMenu() {
  const [menu, setMenu] = useState<MenuRequest | null>(null)
  const open = useCallback((event: ReactMouseEvent<HTMLElement>, label: string, items: MenuEntry[]) => {
    event.preventDefault()
    event.stopPropagation()
    setMenu({ ...menuPoint(event), label, items })
  }, [])
  const close = useCallback(() => setMenu(null), [])
  return { menu, open, close }
}

const MARGIN = 6

/** A floating menu of actions; arrow keys move, Enter picks, Escape or a click outside closes. */
export function ContextMenu({ menu, onClose }: { menu: MenuRequest; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({ left: menu.x, top: menu.y })
  const close = useRef(onClose)
  close.current = onClose

  // Keep the whole menu on screen: flip left of / above the point if needed.
  useLayoutEffect(() => {
    const element = panel.current
    if (!element) return
    const { width, height } = element.getBoundingClientRect()
    const left = menu.x + width + MARGIN > window.innerWidth ? Math.max(MARGIN, menu.x - width) : menu.x
    const top = menu.y + height + MARGIN > window.innerHeight ? Math.max(MARGIN, menu.y - height) : menu.y
    setPosition({ left, top })
  }, [menu])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    panel.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus()
    const outside = (event: Event) => {
      if (!panel.current?.contains(event.target as Node)) close.current()
    }
    const dismiss = () => close.current()
    document.addEventListener('pointerdown', outside, true)
    document.addEventListener('scroll', dismiss, true)
    window.addEventListener('resize', dismiss)
    window.addEventListener('blur', dismiss)
    return () => {
      document.removeEventListener('pointerdown', outside, true)
      document.removeEventListener('scroll', dismiss, true)
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('blur', dismiss)
      if (previous?.isConnected) previous.focus({ preventScroll: true })
    }
  }, [])

  const onKeyDown = (event: ReactKeyboardEvent) => {
    const buttons = [...(panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    let next: number | null = null
    if (event.key === 'ArrowDown') next = (index + 1) % buttons.length
    else if (event.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = buttons.length - 1
    else if (event.key === 'Escape' || event.key === 'Tab') {
      // Handled here so an enclosing dialog stays open.
      event.preventDefault()
      event.stopPropagation()
      close.current()
      return
    }
    if (next !== null && buttons.length) {
      event.preventDefault()
      buttons[next].focus()
    }
  }

  return createPortal(
    <div
      className="context-menu"
      role="menu"
      aria-label={menu.label}
      ref={panel}
      style={position}
      onKeyDown={onKeyDown}
      onContextMenu={(event) => event.preventDefault()}
    >
      {menu.items.map((item, index) => {
        if (item === 'separator') return <div className="menu-sep" role="separator" key={`sep-${index}`} />
        const Icon = item.icon
        return (
          <button
            type="button"
            role="menuitem"
            key={item.label}
            disabled={item.disabled}
            onClick={() => {
              close.current()
              item.onSelect()
            }}
          >
            {Icon ? <Icon size={14} /> : <span className="menu-icon-gap" />}
            {item.label}
          </button>
        )
      })}
    </div>,
    document.body,
  )
}
