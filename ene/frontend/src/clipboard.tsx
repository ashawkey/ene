import { useEffect, useRef, useState } from 'react'

/**
 * Copy text to the clipboard. The async Clipboard API exists only in secure
 * contexts, and the hub is often opened over plain HTTP on a LAN address, so
 * fall back to a hidden textarea and the legacy copy command.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Permission denied or unavailable: try the legacy path below.
  }
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
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

export function CopyButton({
  text,
  label = 'Copy',
  className = 'copy-button',
}: {
  text: string | (() => string)
  label?: string
  className?: string
}) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<number | null>(null)
  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
  }, [])
  const title = state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : label
  return (
    <button
      type="button"
      className={`${className}${state === 'idle' ? '' : ` ${state}`}`}
      aria-label={title}
      title={title}
      onClick={async (event) => {
        event.stopPropagation()
        const ok = await copyText(typeof text === 'function' ? text() : text)
        setState(ok ? 'copied' : 'failed')
        if (timer.current !== null) window.clearTimeout(timer.current)
        timer.current = window.setTimeout(() => setState('idle'), 1400)
      }}
    >
      {state === 'copied' ? '✓' : state === 'failed' ? '!' : '⧉'}
    </button>
  )
}
