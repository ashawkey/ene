export function compactNumber(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1e9) return `${trim(value / 1e9)}B`
  if (abs >= 1e6) return `${trim(value / 1e6)}M`
  if (abs >= 1e3) return `${trim(value / 1e3)}k`
  return String(Math.round(value))
}

function trim(value: number) {
  return value >= 100 ? value.toFixed(0) : value >= 10 ? value.toFixed(1).replace(/\.0$/, '') : value.toFixed(2).replace(/\.?0+$/, '')
}

export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0s'
  if (seconds < 10) return `${seconds.toFixed(1)}s`
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  if (size < 1024 ** 3) return `${(size / 1024 / 1024).toFixed(1)} MB`
  return `${(size / 1024 ** 3).toFixed(1)} GB`
}

export function clockTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Time of day, prefixed with the date when it is not today. */
export function messageClock(epochSeconds: number, now = Date.now()): string {
  const date = new Date(epochSeconds * 1000)
  const time = clockTime(epochSeconds)
  if (date.toDateString() === new Date(now).toDateString()) return time
  return `${date.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
}

/** Full local date and time, for tooltips. */
export function fullTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString()
}

export function relativeTime(epochSeconds: number, now = Date.now() / 1000): string {
  const delta = Math.max(0, now - epochSeconds)
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return new Date(epochSeconds * 1000).toLocaleDateString()
}

export function basename(path: string): string {
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/)
  return parts.at(-1) || path
}

/** Display title shared by the sidebar and pane header: name, else the session ID. */
export function sessionTitle(session: { name?: string; title?: string; id: string }): string {
  return session.name || session.title || session.id
}

export function percent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

/** Short latency: milliseconds under a second, else a duration. */
export function latency(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0ms'
  return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : duration(seconds)
}

export function tokenRate(perSecond: number): string {
  return `${perSecond >= 100 ? Math.round(perSecond) : perSecond.toFixed(1).replace(/\.0$/, '')} tok/s`
}
