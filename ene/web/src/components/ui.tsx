import type { LucideIcon } from 'lucide-react'
import { CircleAlert, CircleCheck, Info, LoaderCircle, X } from 'lucide-react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'

import type { LinkStatus } from '../lib/socket'
import { dismiss, useToasts } from '../lib/toast'

export const ICON = { size: 18, strokeWidth: 1.8 }

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon: LucideIcon
  label: string
  active?: boolean
  badge?: number | string | null
  busy?: boolean
  tone?: 'default' | 'danger' | 'accent'
  size?: 'sm' | 'md'
}

/** Icon-only button; *label* is both the accessible name and the tooltip. */
export function IconButton({
  icon: Icon,
  label,
  active,
  badge,
  busy,
  tone = 'default',
  size = 'md',
  className = '',
  ...rest
}: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      data-tip={label}
      aria-pressed={active === undefined ? undefined : active}
      className={`icon-btn ${size} ${tone} ${active ? 'active' : ''} ${className}`}
      {...rest}
    >
      {busy ? <LoaderCircle {...ICON} size={size === 'sm' ? 15 : 18} className="spin" /> : <Icon {...ICON} size={size === 'sm' ? 15 : 18} />}
      {badge ? <span className="badge">{badge}</span> : null}
    </button>
  )
}

export function Spinner({ size = 16 }: { size?: number }) {
  return <LoaderCircle size={size} strokeWidth={2} className="spin" aria-hidden />
}

/** Circular context-window gauge. */
export function ContextRing({ fraction, size = 22 }: { fraction: number | null; size?: number }) {
  const radius = size / 2 - 2.5
  const circumference = 2 * Math.PI * radius
  const value = fraction ?? 0
  const tone = value >= 0.9 ? 'high' : value >= 0.75 ? 'mid' : 'low'
  return (
    <svg className={`ring ${tone}`} width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
      <circle className="ring-track" cx={size / 2} cy={size / 2} r={radius} />
      <circle
        className="ring-value"
        cx={size / 2}
        cy={size / 2}
        r={radius}
        strokeDasharray={`${circumference * value} ${circumference}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  )
}

const statusText: Record<LinkStatus, string> = {
  open: 'Connected',
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  offline: 'Offline — waiting for network',
  closed: 'Disconnected',
}

export function LinkDot({ status }: { status: LinkStatus }) {
  return <span className={`link-dot ${status}`} role="status" aria-label={statusText[status]} data-tip={statusText[status]} />
}

/** Banner shown only after a link has been down for a moment (no flicker). */
export function LinkBanner({ status, onRetry }: { status: LinkStatus; onRetry: () => void }) {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    if (status === 'open') {
      setVisible(false)
      return
    }
    const timer = window.setTimeout(() => setVisible(true), 900)
    return () => window.clearTimeout(timer)
  }, [status])
  if (!visible || status === 'open') return null
  return (
    <div className={`link-banner ${status}`} role="status">
      {status === 'offline' || status === 'closed' ? <CircleAlert size={15} /> : <Spinner size={14} />}
      <span>{statusText[status]}</span>
      <button type="button" onClick={onRetry}>Retry now</button>
    </div>
  )
}

export function Modal({
  children,
  onClose,
  className = '',
  label,
}: {
  children: ReactNode
  onClose: () => void
  className?: string
  label: string
}) {
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        event.preventDefault()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    panel.current?.focus()
    return () => {
      window.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [onClose])
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className={`modal glass ${className}`} role="dialog" aria-modal="true" aria-label={label} tabIndex={-1} ref={panel}>
        {children}
      </div>
    </div>
  )
}

export function Toasts() {
  const toasts = useToasts()
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((item) => {
        const Icon = item.tone === 'error' ? CircleAlert : item.tone === 'success' ? CircleCheck : Info
        return (
          <div key={item.id} className={`toast glass ${item.tone}`} role={item.tone === 'error' ? 'alert' : 'status'}>
            <Icon size={16} />
            <span>{item.text}</span>
            <button type="button" aria-label="Dismiss" onClick={() => dismiss(item.id)}><X size={14} /></button>
          </div>
        )
      })}
    </div>
  )
}

/** Live-updating clock for elapsed timers; re-renders once per second. */
export function useNow(active: boolean, interval = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), interval)
    return () => window.clearInterval(timer)
  }, [active, interval])
  return now
}

export function Empty({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="empty">
      <Icon size={28} strokeWidth={1.4} />
      <div>{children}</div>
    </div>
  )
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Insecure contexts (plain http over LAN) have no async clipboard.
    const area = document.createElement('textarea')
    area.value = text
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    area.remove()
    return ok
  }
}
