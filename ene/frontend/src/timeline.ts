import type { DisplayEvent } from './types'

// Activity events that may fold into a group. Diffs, messages, and notices
// always stay visible on their own.
const groupableTypes = new Set(['tool_start', 'tool_result', 'output', 'debug', 'thinking'])

// A run folds only when it holds at least this many tool calls; short runs
// read fine inline and folding them would only add clicks.
export const GROUP_MIN_CALLS = 3

export type TimelineItem =
  | { kind: 'event'; event: DisplayEvent }
  | { kind: 'group'; key: string; events: DisplayEvent[] }

/** Fold long runs of consecutive tool activity into groups. */
export function groupTimeline(events: DisplayEvent[]): TimelineItem[] {
  const items: TimelineItem[] = []
  let run: DisplayEvent[] = []
  const flush = () => {
    const calls = run.filter((event) => event.type === 'tool_start').length
    if (calls >= GROUP_MIN_CALLS) {
      items.push({ kind: 'group', key: `group-${run[0].key}`, events: run })
    } else {
      for (const event of run) items.push({ kind: 'event', event })
    }
    run = []
  }
  for (const event of events) {
    if (groupableTypes.has(event.type)) {
      run.push(event)
    } else {
      flush()
      items.push({ kind: 'event', event })
    }
  }
  flush()
  return items
}

export type GroupSummary = {
  calls: number
  failed: number
  tools: Array<[string, number]>
  latest: string
}

export function summarizeGroup(events: DisplayEvent[]): GroupSummary {
  const counts = new Map<string, number>()
  let calls = 0
  let failed = 0
  let latest = ''
  for (const event of events) {
    if (event.type === 'tool_start') {
      calls += 1
      const name = typeof event.data.name === 'string' && event.data.name
        ? event.data.name
        : event.text.split(/[\s:(]/, 1)[0] || 'tool'
      counts.set(name, (counts.get(name) ?? 0) + 1)
      const primary = typeof event.data.primary === 'string' ? event.data.primary : ''
      latest = primary ? `${name} ${primary}` : name
    } else if (event.type === 'tool_result' && event.data.success === false) {
      failed += 1
    }
  }
  const tools = [...counts.entries()].sort((a, b) => b[1] - a[1])
  return { calls, failed, tools, latest }
}

/** Every string a reader could see for an event, joined for searching. */
export function searchableText(event: DisplayEvent): string {
  const { data } = event
  const parts = [event.text]
  for (const value of [data.name, data.primary, data.path, data.old_text, data.new_text]) {
    if (typeof value === 'string') parts.push(value)
  }
  if (Array.isArray(data.qualifiers)) {
    for (const value of data.qualifiers) if (typeof value === 'string') parts.push(value)
  }
  return parts.join('\n')
}

/** Keys of events whose visible text contains *query* (case-insensitive). */
export function findMatches(events: DisplayEvent[], query: string): string[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  return events
    .filter((event) => searchableText(event).toLowerCase().includes(needle))
    .map((event) => event.key)
}
