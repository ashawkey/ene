import {
  ArrowDown,
  Bot,
  ChevronsUp,
  CircleCheck,
  CircleX,
  ListFilter,
  MessagesSquare,
  RefreshCw,
  ScrollText,
  Square,
  SquareTerminal,
  WrapText,
  X,
} from 'lucide-react'
import { memo, useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { stripAnsi } from '../lib/ansi'
import { errorMessage, stopProcess } from '../lib/api'
import { bytes, compactNumber, duration } from '../lib/format'
import { NO_AUTOFILL } from '../lib/noAutofill'
import type { LogWindow, SubagentResult } from '../lib/processOutput'
import { logLines, logStart, logText, useProcessEvents, useProcessLog } from '../lib/processOutput'
import type { ProcessActivity } from '../lib/protocol'
import { toast } from '../lib/toast'
import { CopyButton } from './Markdown'
import type { TimelineHandle } from './Timeline'
import { Timeline } from './Timeline'
import { AnsiText } from './TimelineItems'
import { Empty, IconButton, Modal, Spinner, useNow } from './ui'

type Mode = 'chat' | 'log'

const LINES_PER_BLOCK = 400
const PIN_DISTANCE = 80
const noop = () => {}

/**
 * Full output of one managed process. Subagents (processes that record agent
 * events) open as a read-only conversation, with the raw log one toggle away.
 */
export const ProcessViewer = memo(function ProcessViewer({
  sessionId,
  process,
  receivedAt,
  connected,
  onClose,
  onOpenFile,
}: {
  sessionId: string
  process: ProcessActivity
  /** When *process* was last pushed, to keep a running timer ticking. */
  receivedAt: number
  connected: boolean
  onClose: () => void
  onOpenFile: (path: string) => void
}) {
  const agent = process.kind === 'agent'
  const running = process.status !== 'exited'
  const [mode, setMode] = useState<Mode>(agent ? 'chat' : 'log')
  const showChat = agent && mode === 'chat'
  const [wrap, setWrap] = useState(true)
  const [filter, setFilter] = useState('')
  const [armed, setArmed] = useState(false)
  const [stopping, setStopping] = useState(false)
  const now = useNow(running)
  const elapsed = process.elapsed_seconds + (running ? Math.max(0, (now - receivedAt) / 1000) : 0)

  const log = useProcessLog(sessionId, process.process_id, process.log_bytes ?? 0, connected && !showChat)
  const events = useProcessEvents(sessionId, process.process_id, process.events_bytes ?? 0, connected && showChat)

  const stop = async () => {
    if (!armed) {
      setArmed(true)
      window.setTimeout(() => setArmed(false), 3000)
      return
    }
    setArmed(false)
    setStopping(true)
    try {
      await stopProcess(sessionId, process.process_id)
    } catch (exc) {
      toast(errorMessage(exc, 'Could not stop the process.'))
    } finally {
      setStopping(false)
    }
  }

  const status = running
    ? 'running'
    : process.exit_code === null || process.exit_code === undefined ? 'exited' : `exited (${process.exit_code})`
  const failed = !running && typeof process.exit_code === 'number' && process.exit_code !== 0
  const Icon = agent ? Bot : SquareTerminal

  return (
    <Modal onClose={onClose} className="viewer proc-viewer" label={`Process ${process.label}`}>
      <div className="viewer-head">
        <Icon size={16} />
        <div className="viewer-title">
          <span className="clamp">{process.label}</span>
          <span className="muted small">
            <span className={running ? 'ok' : failed ? 'fail' : ''}>{status}</span>
            {' · '}{duration(elapsed)}{' · '}#{process.process_id}
            {process.log_bytes ? ` · ${bytes(process.log_bytes)}` : ''}
          </span>
        </div>
        <span className="grow" />
        {agent ? (
          <div className="segmented" role="tablist" aria-label="View">
            <button type="button" role="tab" aria-selected={showChat} className={showChat ? 'on' : ''} onClick={() => setMode('chat')} data-tip="Conversation">
              <MessagesSquare size={14} />
            </button>
            <button type="button" role="tab" aria-selected={!showChat} className={!showChat ? 'on' : ''} onClick={() => setMode('log')} data-tip="Raw log">
              <ScrollText size={14} />
            </button>
          </div>
        ) : null}
        {!showChat ? (
          <>
            <label className="log-filter" data-tip="Show only matching lines">
              <ListFilter size={14} />
              <input {...NO_AUTOFILL} value={filter} placeholder="Filter" aria-label="Filter lines" onChange={(event) => setFilter(event.target.value)} />
            </label>
            <IconButton icon={WrapText} size="sm" label={wrap ? 'Disable line wrap' : 'Wrap lines'} active={wrap} onClick={() => setWrap(!wrap)} />
            <CopyButton text={stripAnsi(logText(log.log))} label="Copy loaded output" />
          </>
        ) : null}
        {running ? (
          <IconButton
            icon={Square}
            size="sm"
            tone="danger"
            active={armed}
            busy={stopping}
            disabled={!connected}
            label={armed ? 'Click again to stop' : 'Stop process'}
            onClick={stop}
          />
        ) : null}
        <IconButton icon={X} size="sm" label="Close" onClick={onClose} />
      </div>
      {process.command ? <div className="proc-command mono clamp" data-tip={process.command}>$ {process.command}</div> : null}
      <div className="proc-body">
        {showChat ? (
          <ChatView
            view={events.feed.view}
            result={events.feed.result}
            loaded={events.loaded}
            running={running}
            error={events.error}
            onRetry={events.retry}
            onOpenFile={onOpenFile}
          />
        ) : (
          <LogView
            log={log.log}
            total={process.log_bytes ?? 0}
            wrap={wrap}
            filter={filter}
            error={log.error}
            onRetry={log.retry}
            onLoadEarlier={log.loadEarlier}
            loadingEarlier={log.loadingEarlier}
          />
        )}
      </div>
    </Modal>
  )
})

function ErrorBar({ error, onRetry }: { error: string; onRetry: () => void }) {
  return (
    <div className="proc-error">
      <span>{error}</span>
      <IconButton icon={RefreshCw} size="sm" label="Retry" onClick={onRetry} />
    </div>
  )
}

function ChatView({
  view,
  result,
  loaded,
  running,
  error,
  onRetry,
  onOpenFile,
}: {
  view: ReturnType<typeof useProcessEvents>['feed']['view']
  result: SubagentResult | null
  loaded: boolean
  running: boolean
  error: string
  onRetry: () => void
  onOpenFile: (path: string) => void
}) {
  const handle = useRef<TimelineHandle | null>(null)
  // Read-only: the run is live exactly while its process is.
  const shown = useMemo(
    () => ({ ...view, operationId: running ? 'subagent' : null, prompt: null, pending: null }),
    [view, running],
  )
  return (
    <>
      {error ? <ErrorBar error={error} onRetry={onRetry} /> : null}
      {!loaded && !error ? (
        <div className="proc-loading"><Spinner /></div>
      ) : (
        <Timeline
          view={shown}
          active
          handle={handle}
          query=""
          matchIndex={0}
          onMatches={noop}
          onOpenFile={onOpenFile}
          onOpenStats={noop}
        />
      )}
      {result ? (
        <div className={`proc-result ${result.success ? 'ok' : 'fail'}`}>
          {result.success ? <CircleCheck size={14} /> : <CircleX size={14} />}
          <span>{result.success ? 'Completed' : result.outcome.replace(/_/g, ' ') || 'Failed'}</span>
          {result.tokens ? <span className="muted">· {compactNumber(result.tokens)} tokens</span> : null}
          {!result.success && result.error ? <span className="clamp muted">· {result.error}</span> : null}
        </div>
      ) : running ? (
        <div className="proc-result live"><Spinner size={13} /><span className="muted">Working…</span></div>
      ) : null}
    </>
  )
}

const LogBlock = memo(function LogBlock({ text }: { text: string }) {
  return <pre className="log-block ansi"><AnsiText text={text} /></pre>
})

function LogView({
  log,
  total,
  wrap,
  filter,
  error,
  onRetry,
  onLoadEarlier,
  loadingEarlier,
}: {
  log: LogWindow
  total: number
  wrap: boolean
  filter: string
  error: string
  onRetry: () => void
  onLoadEarlier: () => void
  loadingEarlier: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  const [away, setAway] = useState(false)
  const lines = useMemo(() => logLines(logText(log)), [log])
  const needle = filter.trim().toLowerCase()
  const shown = useMemo(
    () => (needle ? lines.filter((line) => stripAnsi(line.text).toLowerCase().includes(needle)) : lines),
    [lines, needle],
  )
  const blocks = useMemo(() => {
    const result: Array<{ key: number; text: string }> = []
    for (let index = 0; index < shown.length; index += LINES_PER_BLOCK) {
      const slice = shown.slice(index, index + LINES_PER_BLOCK)
      result.push({ key: slice[0].index, text: slice.map((line) => line.text).join('\n') })
    }
    return result
  }, [shown])
  const start = logStart(log)

  // Follow the tail while pinned; keep the reading position when earlier
  // output is inserted above it.
  const previous = useRef<{ start: number | null; height: number }>({ start: null, height: 0 })
  useLayoutEffect(() => {
    const element = scroller.current
    if (!element) return
    const before = previous.current
    if (before.start !== null && start !== null && start < before.start && !pinned.current) {
      element.scrollTop += element.scrollHeight - before.height
    } else if (pinned.current) {
      element.scrollTop = element.scrollHeight
    }
    previous.current = { start, height: element.scrollHeight }
  }, [blocks, start])

  const onScroll = useCallback(() => {
    const element = scroller.current
    if (!element) return
    const distance = element.scrollHeight - element.clientHeight - element.scrollTop
    pinned.current = distance < PIN_DISTANCE
    setAway(distance > 400)
  }, [])

  const toBottom = () => {
    const element = scroller.current
    if (!element) return
    pinned.current = true
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
  }

  return (
    <>
      {error ? <ErrorBar error={error} onRetry={onRetry} /> : null}
      <div className={`log-scroller ${wrap ? 'wrap' : ''}`} ref={scroller} onScroll={onScroll}>
        {start ? (
          <button type="button" className="log-earlier" disabled={loadingEarlier} onClick={onLoadEarlier}>
            {loadingEarlier ? <Spinner size={13} /> : <ChevronsUp size={14} />}
            Load earlier output <span className="muted">({bytes(start)} above)</span>
          </button>
        ) : null}
        {blocks.length ? blocks.map((block) => <LogBlock key={block.key} text={block.text} />) : (
          !error ? (
            <Empty icon={ScrollText}>
              {needle && lines.length ? 'No matching lines.' : total > 0 && log.chunks.length === 0 ? 'Loading…' : 'No output yet.'}
            </Empty>
          ) : null
        )}
      </div>
      {away ? (
        <button type="button" className="jump-latest glass" onClick={toBottom} aria-label="Jump to latest">
          <ArrowDown size={16} />
        </button>
      ) : null}
    </>
  )
}
