import { diffLines } from 'diff'
import {
  Bell,
  Bot,
  Brain,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleX,
  Coins,
  Cpu,
  FileDiff,
  Gauge,
  Hourglass,
  Info,
  Layers,
  Monitor,
  ScrollText,
  Terminal,
  Timer,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { createContext, memo, useContext, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { parseAnsi } from '../lib/ansi'
import { rawFileUrl } from '../lib/api'
import { clockTime, compactNumber, duration, latency, tokenRate } from '../lib/format'
import { highlight, languageForPath } from '../lib/highlight'
import type { DiffData, TimelineItem, ToolImage } from '../lib/session'
import { summarize } from '../lib/timeline'
import type { ProcessActivity, RoundStats } from '../lib/protocol'
import { CopyButton, Markdown } from './Markdown'
import { Spinner } from './ui'

type ItemOf<K extends TimelineItem['kind']> = Extract<TimelineItem, { kind: K }>

/** Bodies up to this many lines start open; longer ones start folded. */
export const AUTO_FOLD_LINES = 12

const lineCount = (text: string) => (text ? text.split('\n').length : 0)

/**
 * One row of agent activity: chevron, icon, label, then an indented body.
 * Thinking, tool calls, and printed output all share this layout so they
 * align and fold the same way. *autoOpen* applies until the reader toggles.
 */
function Step({
  icon,
  label,
  children,
  autoOpen,
  preview,
  className = '',
}: {
  icon: ReactNode
  label: ReactNode
  children?: ReactNode
  autoOpen: boolean
  /** Shown in place of the body while folded. */
  preview?: ReactNode
  className?: string
}) {
  const [manual, setManual] = useState<boolean | null>(null)
  const foldable = children !== undefined && children !== null && children !== false
  const open = foldable && (manual ?? autoOpen)
  const head = (
    <>
      <span className="step-chev">{foldable ? <ChevronRight size={14} className="chev" /> : null}</span>
      <span className="step-icon">{icon}</span>
      <span className="step-label">{label}</span>
    </>
  )
  return (
    <div className={`step ${open ? 'open' : ''} ${className}`}>
      {foldable
        ? <button type="button" className="step-head" aria-expanded={open} onClick={() => setManual(!open)}>{head}</button>
        : <div className="step-head">{head}</div>}
      {open ? <div className="step-body">{children}</div> : preview ? <div className="step-body">{preview}</div> : null}
    </div>
  )
}

/** A monospace block with a hover copy button; *follow* keeps the tail in view. */
function OutputBlock({ text, ansi, follow = false }: { text: string; ansi?: string | null; follow?: boolean }) {
  const ref = useRef<HTMLPreElement>(null)
  useLayoutEffect(() => {
    if (follow && ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [follow, text])
  return (
    <div className="output-wrap">
      <pre className="output ansi" ref={ref}>{ansi ? <AnsiText text={ansi} /> : text}</pre>
      <CopyButton text={text} label="Copy output" />
    </div>
  )
}

const sourceIcon = (source: string) =>
  source === 'terminal' ? <Terminal size={12} /> : source === 'web' ? <Monitor size={12} /> : null

export const UserMessage = memo(function UserMessage({ item }: { item: ItemOf<'user'> }) {
  if (item.source === 'inbox') {
    return (
      <div className="inbox-note">
        <Bell size={14} />
        <span className="clamp">{item.text}</span>
      </div>
    )
  }
  return (
    <div className="user-row">
      <div className="user-bubble">
        <div className="user-text">{item.text}</div>
      </div>
      {/* Outside the bubble, so the hover background is never clipped by its corner. */}
      <div className="bubble-meta">
        {sourceIcon(item.source)}
        {item.steer ? <span className="pill-mini">steer</span> : null}
        <span>{clockTime(item.ts)}</span>
        <CopyButton text={item.text} label="Copy message" />
      </div>
    </div>
  )
})

export const AssistantMessage = memo(function AssistantMessage({
  item,
  round,
  onOpenStats,
}: {
  item: ItemOf<'assistant'>
  round?: RoundStats
  onOpenStats?: () => void
}) {
  return (
    <div className={`assistant ${item.streaming ? 'streaming' : ''}`}>
      <Markdown text={item.text} streaming={item.streaming} />
      {!item.streaming && (item.text || round) ? (
        <div className="reply-foot">
          {item.text ? <CopyButton text={item.text} label="Copy reply" /> : null}
          {item.text ? <span className="reply-time">{clockTime(item.ts)}</span> : null}
          {round ? <RoundChip round={round} onOpen={onOpenStats} /> : null}
        </div>
      ) : null}
    </div>
  )
})

/** The newest lines of a streaming thought, anchored to the bottom. */
function ThinkingTail({ text }: { text: string }) {
  return (
    <div className="thinking-live" aria-hidden="true">
      <div className="thinking-live-text">{text.slice(-THINKING_TAIL_CHARS)}</div>
    </div>
  )
}

const THINKING_TAIL_CHARS = 800

export const Thinking = memo(function Thinking({ item }: { item: ItemOf<'thinking'> }) {
  const firstLine = item.text.trim().split('\n', 1)[0] ?? ''
  // Folded by default; while streaming the folded row previews the newest
  // lines, and unfolding shows the whole thought so far.
  return (
    <Step
      className={`thinking ${item.streaming ? 'live' : ''}`}
      icon={<Brain size={14} />}
      label={item.streaming
        ? <span className="thinking-label">Thinking<span className="dots" aria-hidden="true" /></span>
        : <span className="clamp">{firstLine || 'Thought'}</span>}
      autoOpen={false}
      preview={item.streaming && item.text.trim() ? <ThinkingTail text={item.text} /> : null}
    >
      {item.text.trim()
        ? <div className="thinking-text"><Markdown text={item.text} streaming={item.streaming} /></div>
        : null}
    </Step>
  )
})

/** The session whose workspace serves tool images; null disables previews. */
export const SessionIdContext = createContext<string | null>(null)

/** Preview of an image a tool loaded, served read-only from the workspace. */
function ToolImagePreview({ image, onOpenFile }: { image: ToolImage; onOpenFile?: (path: string) => void }) {
  const sessionId = useContext(SessionIdContext)
  const [failed, setFailed] = useState(false)
  if (!sessionId) return null
  if (failed) return <div className="step-note muted">Image preview unavailable (outside the workspace or unsupported).</div>
  const img = <img src={rawFileUrl(sessionId, image.path)} alt={image.path} loading="lazy" onError={() => setFailed(true)} />
  return onOpenFile ? (
    <button type="button" className="tool-image" onClick={() => onOpenFile(image.path)} data-tip="Open image">{img}</button>
  ) : <div className="tool-image">{img}</div>
}

export type ProcessLinks = {
  find: (processId: string) => ProcessActivity | null
  open: (processId: string) => void
}
/** Lets a `start_process` card open that process's output viewer. */
export const OpenProcessContext = createContext<ProcessLinks | null>(null)

/** The process a `start_process` card launched, if it is still listed. */
function useLaunchedProcess(item: ItemOf<'tool'>): ProcessActivity | null {
  const links = useContext(OpenProcessContext)
  if (!links || item.name !== 'start_process' || !item.result?.success) return null
  // The result summary starts with "<id> · <status> · pid …".
  const match = /^(\S+) · (?:running|exited)/.exec(item.result.text)
  const process = match ? links.find(match[1]) : null
  // IDs restart with each worker; require the label to match too.
  return process && item.primary.includes(process.label.slice(0, 24)) ? process : null
}

export const ToolCall = memo(function ToolCall({
  item,
  live,
  onOpenFile,
}: {
  item: ItemOf<'tool'>
  live: boolean
  onOpenFile?: (path: string) => void
}) {
  const running = item.result === null && live
  const failed = item.result?.success === false
  const icon = item.result === null
    ? (live ? <Spinner size={14} /> : <Wrench size={14} />)
    : failed
      ? <CircleX size={14} className="fail" />
      : <CircleCheck size={14} className="ok" />
  const label = (
    <span className="tool-head">
      <span className="tool-name">{item.name || 'tool'}</span>
      {item.primary ? <span className="tool-primary">{item.primary}</span> : null}
      {!item.name && item.text ? <span className="tool-primary">{item.text}</span> : null}
      {item.qualifiers.map((qualifier) => <span className="tool-qual" key={qualifier}>{qualifier}</span>)}
    </span>
  )
  const output = item.output?.text.trim() ? item.output : null
  const result = item.result?.text.trim() ?? ''
  const launched = useLaunchedProcess(item)
  const links = useContext(OpenProcessContext)
  const image = item.result?.image ?? null
  const empty = !output && !result && !image && item.diffs.length === 0
  // File edits start folded; text folds past a few lines. Failures stay open.
  const lines = lineCount(output?.text ?? '') + lineCount(result)
  const autoOpen = failed || (item.diffs.length === 0 && (running || lines <= AUTO_FOLD_LINES))
  return (
    <Step className={`tool ${failed ? 'failed' : ''}`} icon={icon} label={label} autoOpen={autoOpen}>
      {empty ? null : (
        <>
          {output ? <OutputBlock text={output.text} ansi={output.ansi} follow={running} /> : null}
          {item.diffs.map((diff, index) => <DiffCard diff={diff} key={index} onOpenFile={onOpenFile} />)}
          {/* With output shown, the result is only its summary line. */}
          {result && output && !result.includes('\n') ? <div className="step-note">{result}</div>
            : result ? <OutputBlock text={result} /> : null}
          {image ? <ToolImagePreview image={image} onOpenFile={onOpenFile} /> : null}
          {launched && links ? (
            <button type="button" className="link proc-link" onClick={() => links.open(launched.process_id)}>
              {launched.kind === 'agent' ? <><Bot size={14} />Open subagent conversation</> : <><ScrollText size={14} />View full output</>}
            </button>
          ) : null}
        </>
      )}
    </Step>
  )
})

type DiffRow = { sign: ' ' | '+' | '-'; text: string; oldNo: number | null; newNo: number | null }

function diffRows(oldText: string, newText: string, start: number): DiffRow[] {
  const rows: DiffRow[] = []
  let oldNo = start
  let newNo = start
  for (const part of diffLines(oldText, newText)) {
    const lines = part.value.replace(/\n$/, '').split('\n')
    for (const text of lines) {
      if (part.added) rows.push({ sign: '+', text, oldNo: null, newNo: newNo++ })
      else if (part.removed) rows.push({ sign: '-', text, oldNo: oldNo++, newNo: null })
      else rows.push({ sign: ' ', text, oldNo: oldNo++, newNo: newNo++ })
    }
  }
  return rows
}

export const DiffCard = memo(function DiffCard({ diff: item, onOpenFile }: { diff: DiffData; onOpenFile?: (path: string) => void }) {
  const rows = useMemo(() => diffRows(item.oldText, item.newText, item.line ?? 1), [item.oldText, item.newText, item.line])
  const language = languageForPath(item.path)
  const added = rows.filter((row) => row.sign === '+').length
  const removed = rows.filter((row) => row.sign === '-').length
  const [expanded, setExpanded] = useState(rows.length <= 40)
  const shown = expanded ? rows : rows.slice(0, 24)
  // Highlight only the rows shown, once each; expanding reuses earlier work.
  const html = useMemo(
    () => (expanded ? rows : rows.slice(0, 24)).map((row) => highlight(row.text, language)),
    [rows, expanded, language],
  )
  return (
    <div className={`diff glass-soft ${item.success ? '' : 'failed'}`}>
      <div className="diff-head">
        <FileDiff size={14} />
        <button type="button" className="link mono clamp" onClick={() => onOpenFile?.(item.path)} data-tip="Open file">
          {item.path}
        </button>
        {item.line !== null ? <span className="muted">:{item.line}</span> : null}
        {item.count > 1 ? <span className="pill-mini">×{item.count}</span> : null}
        <span className="grow" />
        <span className="diff-stat add">+{added}</span>
        <span className="diff-stat del">−{removed}</span>
        {!item.success ? <CircleX size={14} className="fail" /> : null}
      </div>
      {item.success ? (
        <div className="diff-body">
          {shown.map((row, index) => {
            const rowHtml = html[index] ?? null
            return (
              <div className={`diff-line ${row.sign === '+' ? 'add' : row.sign === '-' ? 'del' : ''}`} key={index}>
                <span className="ln">{row.oldNo ?? ''}</span>
                <span className="ln">{row.newNo ?? ''}</span>
                <span className="sign">{row.sign}</span>
                {rowHtml !== null
                  ? <code className="hljs" dangerouslySetInnerHTML={{ __html: rowHtml || ' ' }} />
                  : <code>{row.text || ' '}</code>}
              </div>
            )
          })}
          {!expanded ? (
            <button type="button" className="diff-more" onClick={() => setExpanded(true)}>
              Show {rows.length - shown.length} more lines
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
})

const ANSI_CLASSES = ['k', 'r', 'g', 'y', 'b', 'm', 'c', 'w', 'K', 'R', 'G', 'Y', 'B', 'M', 'C', 'W']

export function AnsiText({ text }: { text: string }) {
  const spans = useMemo(() => parseAnsi(text), [text])
  return (
    <>
      {spans.map((span, index) => {
        const classes = [
          span.fg !== undefined ? `fg-${ANSI_CLASSES[span.fg]}` : '',
          span.bg !== undefined ? `bg-${ANSI_CLASSES[span.bg]}` : '',
          span.bold ? 'b' : '',
          span.dim ? 'd' : '',
          span.italic ? 'i' : '',
          span.underline ? 'u' : '',
        ].filter(Boolean).join(' ')
        return classes ? <span className={classes} key={index}>{span.text}</span> : span.text
      })}
    </>
  )
}

export const Output = memo(function Output({ item }: { item: ItemOf<'output'> }) {
  const lines = lineCount(item.text)
  return (
    <Step
      className="output-step"
      icon={<Terminal size={14} />}
      label={<span className="muted">{lines === 1 ? 'Output' : `Output · ${lines} lines`}</span>}
      autoOpen={lines <= AUTO_FOLD_LINES}
    >
      <OutputBlock text={item.text} ansi={item.ansi} />
    </Step>
  )
})

export const Notice = memo(function Notice({ item }: { item: ItemOf<'notice'> }) {
  const Icon = item.level === 'error' ? CircleAlert : item.level === 'warning' ? TriangleAlert : Info
  return (
    <div className={`notice ${item.level}`}>
      <Icon size={14} />
      <span className="notice-text">{item.text}</span>
    </div>
  )
})

export function roundTokens(round: RoundStats) {
  return round.input_tokens + round.output_tokens
}

export const RoundChip = memo(function RoundChip({ round, onOpen }: { round: RoundStats; onOpen?: () => void }) {
  const hit = round.input_tokens > 0 ? round.cached_tokens / round.input_tokens : 0
  return (
    <button type="button" className={`round-chip ${round.cancelled ? 'cancelled' : ''}`} onClick={onOpen} data-tip="Round statistics">
        <span><Timer size={12} />{duration(round.duration)}</span>
        {round.model_calls ? <span><Cpu size={12} />model {duration(round.model_time)}</span> : null}
        {round.tool_calls ? (
          <span>
            <Wrench size={12} />{round.tool_calls} tools {duration(round.tool_time)}
            {round.tool_failures ? <em className="fail"> ·{round.tool_failures}✕</em> : null}
          </span>
        ) : null}
        {round.ttft !== null ? <span><Hourglass size={12} />TTFT {latency(round.ttft)}</span> : null}
        {round.tps !== null ? <span><Gauge size={12} />{tokenRate(round.tps)}</span> : null}
        <span><Coins size={12} />↑{compactNumber(round.input_tokens)} ↓{compactNumber(round.output_tokens)}</span>
        {round.input_tokens > 0 ? <span className="muted">{Math.round(hit * 100)}% cached</span> : null}
        {round.cancelled ? <span className="fail">stopped</span> : null}
    </button>
  )
})

type GroupProps = {
  items: TimelineItem[]
  live: boolean
  render: (item: TimelineItem, live: boolean) => ReactNode
}

// Grouping rebuilds each run's array on every update; compare the items
// themselves so settled groups skip re-rendering while the reply streams.
const sameGroup = (a: GroupProps, b: GroupProps) =>
  a.live === b.live &&
  a.render === b.render &&
  a.items.length === b.items.length &&
  a.items.every((item, index) => item === b.items[index])

export const ActivityGroup = memo(function ActivityGroup({ items, live, render }: GroupProps) {
  const [open, setOpen] = useState(false)
  const summary = useMemo(() => summarize(items), [items])
  const tail = live && !open ? items.slice(-2) : []
  const shown = open ? items : tail
  return (
    <div className={`step activity ${open ? 'open' : ''} ${live ? 'live' : ''}`}>
      <button type="button" className="step-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="step-chev"><ChevronRight size={14} className="chev" /></span>
        <span className="step-icon">{live ? <Spinner size={14} /> : <Layers size={14} />}</span>
        <span className="step-label activity-label">
          <span className="activity-count">{summary.tools} tool calls</span>
          <span className="activity-names">
            {summary.names.slice(0, 4).map(([name, count]) => (
              <span key={name} className="pill-mini">{name}{count > 1 ? ` ×${count}` : ''}</span>
            ))}
          </span>
          {summary.failed ? <span className="fail pill-mini">{summary.failed} failed</span> : null}
        </span>
      </button>
      {shown.length
        ? (
          <div className={`step-body activity-body ${open ? '' : 'tail'}`}>
            {shown.map((item) => <div key={item.key} data-key={item.key}>{render(item, live)}</div>)}
          </div>
        )
        : null}
    </div>
  )
}, sameGroup)
