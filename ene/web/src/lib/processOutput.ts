import { useCallback, useEffect, useRef, useState } from 'react'

import { errorMessage, readProcessOutput } from './api'
import type { OutputRange } from './api'
import type { ProcessOutput, ServerEvent } from './protocol'
import type { SessionView } from './session'
import { initialView, reduce } from './session'

/**
 * One contiguous byte range of a process log. Ranges end exactly where the
 * next begins, so an unfinished line continues in the following range.
 */
export type LogChunk = { start: number; end: number; text: string }
/** The part of a log held in memory; *size* is the file size last reported. */
export type LogWindow = { chunks: LogChunk[]; size: number }

export const emptyLog = (): LogWindow => ({ chunks: [], size: 0 })
export const logStart = (log: LogWindow) => log.chunks[0]?.start ?? null
export const logEnd = (log: LogWindow) => log.chunks.at(-1)?.end ?? null
export const logText = (log: LogWindow) => log.chunks.map((chunk) => chunk.text).join('')

/** Characters kept in memory; the far side is dropped beyond this. */
export const MAX_LOG_CHARS = 4_000_000
/** Following further behind than this re-reads the tail instead. */
export const CATCH_UP_BYTES = 2 * 1024 * 1024
const MERGE_CHARS = 64 * 1024

/**
 * Add a fetched range to the window. A range continuing the tail is appended,
 * one ending at the head is prepended, and anything disjoint starts a new
 * window, so the held text is always one gap-free stretch of the file.
 */
export function addLogRange(log: LogWindow, out: ProcessOutput): LogWindow {
  const chunk: LogChunk = { start: out.start, end: out.end, text: out.text ?? '' }
  const first = log.chunks[0]
  const last = log.chunks.at(-1)
  let chunks: LogChunk[]
  let appended = true
  if (!first || !last) {
    chunks = [chunk]
  } else if (chunk.start === last.end) {
    if (chunk.end === chunk.start) return { ...log, size: Math.max(log.size, out.size) }
    chunks = last.text.length < MERGE_CHARS
      ? [...log.chunks.slice(0, -1), { start: last.start, end: chunk.end, text: last.text + chunk.text }]
      : [...log.chunks, chunk]
  } else if (chunk.end === first.start) {
    if (chunk.end === chunk.start) return log
    chunks = [chunk, ...log.chunks]
    appended = false
  } else {
    chunks = [chunk]
  }
  let total = chunks.reduce((sum, item) => sum + item.text.length, 0)
  while (total > MAX_LOG_CHARS && chunks.length > 1) {
    const dropped = appended ? chunks.shift()! : chunks.pop()!
    total -= dropped.text.length
  }
  return { chunks, size: Math.max(out.size, appended ? out.size : log.size) }
}

export type DisplayLine = { index: number; text: string }

/** Split log text into display lines, keeping only each line's last redraw. */
export function logLines(text: string): DisplayLine[] {
  if (!text) return []
  const raw = text.endsWith('\n') ? text.slice(0, -1) : text
  return raw.split('\n').map((line, index) => {
    const trimmed = line.endsWith('\r') ? line.slice(0, -1) : line
    const redraw = trimmed.lastIndexOf('\r')
    return { index, text: redraw >= 0 ? trimmed.slice(redraw + 1) : trimmed }
  })
}

/** Final record the subagent runner appends after its conversation. */
export type SubagentResult = {
  success: boolean
  outcome: string
  error: string | null
  tokens: number
}

export type EventFeed = { view: SessionView; offset: number; result: SubagentResult | null }

export const emptyFeed = (): EventFeed => ({ view: initialView(), offset: 0, result: null })

/** Fold one page of recorded agent events into a conversation view. */
export function addEvents(feed: EventFeed, out: ProcessOutput): EventFeed {
  if (out.start !== feed.offset) return feed
  let view = feed.view
  let result = feed.result
  for (const event of out.events ?? []) {
    if (!event || typeof event !== 'object') continue
    if (event.type === 'subagent_result') {
      const data = (event.data ?? {}) as Record<string, unknown>
      const usage = (data.token_usage ?? {}) as Record<string, unknown>
      result = {
        success: data.success === true,
        outcome: typeof data.outcome === 'string' ? data.outcome : '',
        error: typeof data.error === 'string' ? data.error : null,
        tokens: typeof usage.total === 'number' ? usage.total : 0,
      }
      continue
    }
    view = reduce(view, event as ServerEvent)[0]
  }
  return { view, offset: out.end, result }
}

/**
 * Fetch a growing file only as far as the pushed snapshot says it reaches.
 *
 * There is no polling timer: each snapshot size change (pushed with process
 * status) starts at most one fetch loop, which reads forward until it has
 * caught up or the file ends in an unfinished line.
 */
function usePump(
  enabled: boolean,
  size: number,
  step: (target: number) => Promise<boolean>,
  failure: string,
) {
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const busy = useRef(false)
  const alive = useRef(true)
  const sizeRef = useRef(size)
  sizeRef.current = size
  const stepRef = useRef(step)
  stepRef.current = step

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled || busy.current) return
    busy.current = true
    void (async () => {
      try {
        for (let guard = 0; guard < 256 && alive.current; guard++) {
          const target = sizeRef.current
          const progressed = await stepRef.current(target)
          if (!alive.current) return
          setError('')
          // Stop when caught up, or stalled on an unfinished line with no
          // newer size to aim for.
          if (!progressed && sizeRef.current === target) break
        }
      } catch (exc) {
        if (alive.current) setError(errorMessage(exc, failure))
      } finally {
        busy.current = false
      }
    })()
  }, [enabled, size, retry, failure])

  return { error, retry: useCallback(() => setRetry((value) => value + 1), []) }
}

export function useProcessLog(sessionId: string, processId: string, size: number, enabled: boolean) {
  const [log, setLog] = useState<LogWindow>(emptyLog)
  const logRef = useRef(log)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [earlierError, setEarlierError] = useState('')
  const apply = useCallback((out: ProcessOutput) => {
    logRef.current = addLogRange(logRef.current, out)
    setLog(logRef.current)
  }, [])
  const read = useCallback(
    (range: Omit<OutputRange, 'stream'>) => readProcessOutput(sessionId, processId, { stream: 'log', ...range }),
    [sessionId, processId],
  )

  const step = useCallback(async (target: number) => {
    const current = logRef.current
    const end = logEnd(current)
    const known = Math.max(target, current.size)
    if (end !== null && end >= known) return false
    if (end === null && known <= 0) return false
    // Start at (or jump to) the tail; otherwise continue where we stopped.
    const range = end === null || known - end > CATCH_UP_BYTES ? { before: known } : { offset: end }
    apply(await read(range))
    const next = logRef.current
    return logEnd(next) !== end || next.size > known
  }, [apply, read])

  const pump = usePump(enabled, size, step, 'Could not read the log.')

  const loadEarlier = useCallback(async () => {
    const start = logStart(logRef.current)
    if (!start) return
    setLoadingEarlier(true)
    setEarlierError('')
    try {
      apply(await read({ before: start }))
    } catch (exc) {
      setEarlierError(errorMessage(exc, 'Could not load earlier output.'))
    } finally {
      setLoadingEarlier(false)
    }
  }, [apply, read])

  // Retry whichever read failed; a failed follow is the more urgent one.
  const retry = useCallback(() => {
    if (pump.error || !earlierError) pump.retry()
    else void loadEarlier()
  }, [pump, earlierError, loadEarlier])

  return { log, error: pump.error || earlierError, retry, loadEarlier, loadingEarlier }
}

export function useProcessEvents(sessionId: string, processId: string, size: number, enabled: boolean) {
  const [feed, setFeed] = useState<EventFeed>(emptyFeed)
  const feedRef = useRef(feed)
  const [loaded, setLoaded] = useState(false)

  const step = useCallback(async (target: number) => {
    const offset = feedRef.current.offset
    if (offset >= target) {
      setLoaded(true)
      return false
    }
    const out = await readProcessOutput(sessionId, processId, { stream: 'events', offset })
    feedRef.current = addEvents(feedRef.current, out)
    setFeed(feedRef.current)
    setLoaded(true)
    return feedRef.current.offset !== offset
  }, [sessionId, processId])

  const pump = usePump(enabled, size, step, 'Could not read the conversation.')
  return { feed, loaded, error: pump.error, retry: pump.retry }
}
