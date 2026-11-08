import { KeyboardEvent, useEffect, useRef, useState } from 'react'

import { BACKGROUND_GLYPH, WATCHER_GLYPH, formatDuration } from './components'
import type { ProcessActivity, ProcessStatus } from './types'

/** Wall-clock milliseconds, re-rendering once a second while *enabled*. */
function useNow(enabled: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    setNow(Date.now())
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [enabled])
  return now
}

/**
 * Runtime as of *now*. Status frames carry the runtime when they were sent,
 * so the browser keeps counting between frames instead of freezing.
 */
export function liveElapsed(process: ProcessActivity, receivedAt: number | undefined, now: number) {
  const drift = receivedAt ? Math.max(0, (now - receivedAt) / 1000) : 0
  return process.elapsed_seconds + drift
}

function ProcessRow({
  process,
  receivedAt,
  now,
  connected,
  onInspect,
  onStop,
}: {
  process: ProcessActivity
  receivedAt?: number
  now: number
  connected: boolean
  onInspect: (id: string) => void
  onStop: (id: string) => void
}) {
  // Stopping is destructive, so it takes a second, deliberate click.
  const [confirming, setConfirming] = useState(false)
  useEffect(() => {
    if (!confirming) return
    const id = window.setTimeout(() => setConfirming(false), 3000)
    return () => window.clearTimeout(id)
  }, [confirming])
  return (
    <li className="task-row">
      <div className="task-head">
        <span
          className={process.watching ? 'process-indicator watcher' : 'process-indicator'}
          title={process.watching ? 'Notifies the agent' : 'Runs without notifying the agent'}
        >
          {process.watching ? WATCHER_GLYPH : BACKGROUND_GLYPH}
        </span>
        <code className="task-id">#{process.process_id}</code>
        <strong className="task-label" title={process.label}>{process.label}</strong>
        <time>{formatDuration(liveElapsed(process, receivedAt, now))}</time>
      </div>
      <pre className="task-line">{process.last_line || 'Waiting for output…'}</pre>
      <div className="task-actions">
        <button type="button" disabled={!connected} onClick={() => onInspect(process.process_id)}>
          Show log
        </button>
        <button
          type="button"
          className={confirming ? 'danger confirming' : 'danger'}
          disabled={!connected}
          onClick={() => {
            if (!confirming) {
              setConfirming(true)
              return
            }
            setConfirming(false)
            onStop(process.process_id)
          }}
        >
          {confirming ? 'Confirm stop' : 'Stop'}
        </button>
      </div>
    </li>
  )
}

/** Side drawer for the active session's background processes. */
export function TaskPanel({
  processStatus,
  connected,
  onClose,
  onInspect,
  onStop,
  onListAll,
}: {
  processStatus: ProcessStatus
  connected: boolean
  onClose: () => void
  onInspect: (id: string) => void
  onStop: (id: string) => void
  onListAll: () => void
}) {
  const now = useNow(processStatus.processes.length > 0)
  return (
    <aside className="task-panel" aria-label="Background tasks">
      <header className="panel-header">
        <h2>Background tasks</h2>
        <button type="button" className="panel-close" onClick={onClose} aria-label="Close tasks" title="Close (Esc)">×</button>
      </header>
      <div className="panel-scroll">
        {processStatus.processes.length === 0 ? (
          <p className="panel-empty">No background processes in this session.</p>
        ) : (
          <ul className="task-list">
            {processStatus.processes.map((process) => (
              <ProcessRow
                key={process.process_id}
                process={process}
                receivedAt={processStatus.receivedAt}
                now={now}
                connected={connected}
                onInspect={onInspect}
                onStop={onStop}
              />
            ))}
          </ul>
        )}
        <button type="button" className="panel-link" disabled={!connected} onClick={onListAll}>
          {processStatus.finished > 0
            ? `List all, including ${processStatus.finished} finished`
            : 'List all processes'}
        </button>
      </div>
    </aside>
  )
}

/** In-timeline search, shown under the top bar. */
export function FindBar({
  query,
  count,
  index,
  onQuery,
  onStep,
  onClose,
}: {
  query: string
  count: number
  index: number
  onQuery: (value: string) => void
  onStep: (direction: 1 | -1) => void
  onClose: () => void
}) {
  const field = useRef<HTMLInputElement>(null)
  useEffect(() => {
    field.current?.focus()
    field.current?.select()
  }, [])
  function keyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter') {
      event.preventDefault()
      onStep(event.shiftKey ? -1 : 1)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      onClose()
    }
  }
  const status = !query.trim() ? '' : count === 0 ? 'No matches' : `${index + 1} / ${count}`
  return (
    <div className="find-bar" role="search">
      <input
        ref={field}
        type="search"
        aria-label="Search this conversation"
        placeholder="Search conversation"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        onKeyDown={keyDown}
      />
      <span className="find-status" aria-live="polite">{status}</span>
      <button type="button" onClick={() => onStep(-1)} disabled={count === 0} aria-label="Previous match" title="Previous (Shift+Enter)">↑</button>
      <button type="button" onClick={() => onStep(1)} disabled={count === 0} aria-label="Next match" title="Next (Enter)">↓</button>
      <button type="button" onClick={onClose} aria-label="Close search" title="Close (Esc)">×</button>
    </div>
  )
}
