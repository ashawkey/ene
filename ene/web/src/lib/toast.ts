import { useSyncExternalStore } from 'react'

export type Toast = { id: number; text: string; tone: 'error' | 'info' | 'success' }

let toasts: Toast[] = []
let counter = 0
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())

export function toast(text: string, tone: Toast['tone'] = 'error', ttl = 5000) {
  // Collapse repeats so a flapping connection cannot stack identical toasts.
  if (toasts.some((item) => item.text === text)) return
  const id = ++counter
  toasts = [...toasts, { id, text, tone }].slice(-4)
  emit()
  window.setTimeout(() => dismiss(id), ttl)
}

export function dismiss(id: number) {
  toasts = toasts.filter((item) => item.id !== id)
  emit()
}

export function useToasts() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => toasts,
  )
}
