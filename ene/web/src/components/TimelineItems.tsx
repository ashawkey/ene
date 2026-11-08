import { diffLines } from 'diff'
import {
  Bell,
  Brain,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleX,
  Coins,
  FileDiff,
  Info,
  Layers,
  Monitor,
  Terminal,
  Timer,
  TriangleAlert,
  Wrench,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { memo, useMemo, useState } from 'react'

import { parseAnsi } from '../lib/ansi'
import { clockTime, compactNumber, duration } from '../lib/format'
import { highlight, languageForPath } from '../lib/highlight'
import type { TimelineItem } from '../lib/session'
import { summarize } from '../lib/timeline'
import type { RoundStats } from '../lib/protocol'
import { CopyButton, Markdown } from './Markdown'
import { Spinner } from './ui'

type ItemOf<K extends TimelineItem['kind']> = Extract<TimelineItem, { kind: K }>

const LONG_LINES = 18

function Collapsible({
  summary,
  children,
  defaultOpen = false,
  className = '',
}: {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className={`collapsible ${open ? 'open' : ''} ${className}`}>
      <button type="button" className="collapsible-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} className="chev" />
        {summary}
      </button>
      {open ? <div className="collapsible-body">{children}</div> : null}
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
        <div className="bubble-meta">
          {sourceIcon(item.source)}
          {item.steer ? <span className="pill-mini">steer</span> : null}
          <span>{clockTime(item.ts)}</span>
          <CopyButton text={item.text} label="Copy message" />
        </div>
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
      <Markdown text={item.text} />
      {!item.streaming && (item.text || round) ? (
        <div className="reply-foot">
          {item.text ? <CopyButton text={item.text} label="Copy reply" /> : null}
          {round ? <RoundChip round={round} onOpen={onOpenStats} /> : null}
        </div>
      ) : null}
    </div>
  )
})

export const Thinking = memo(function Thinking({ item }: { item: ItemOf<'thinking'> }) {
  if (item.streaming) {
    return (
      <div className="thinking live">
        <div className="thinking-head"><Brain size={14} /> Thinking</div>
        <div className="thinking-live-text">{item.text.slice(-600)}</div>
      </div>
    )
  }
  const firstLine = item.text.trim().split('\n', 1)[0] ?? ''
  return (
    <Collapsible
      className="thinking"
      summary={<><Brain size={14} /><span className="muted clamp">{firstLine || 'Thought'}</span></>}
    >
      <div className="thinking-text"><Markdown text={item.text} /></div>
    </Collapsible>
  )
})

export const ToolCall = memo(function ToolCall({ item, live }: { item: ItemOf<'tool'>; live: boolean }) {
  const status = item.result === null
    ? (live ? <Spinner size={13} /> : <Wrench size={13} />)
    : item.result.success
      ? <CircleCheck size={13} className="ok" />
      : <CircleX size={13} className="fail" />
  const head = (
    <span className="tool-head">
      <span className="tool-status">{status}</span>
      <span className="tool-name">{item.name || 'tool'}</span>
      {item.primary ? <span className="tool-primary">{item.primary}</span> : null}
      {!item.name && item.text ? <span className="tool-primary">{item.text}</span> : null}
      {item.qualifiers.map((qualifier) => <span className="tool-qual" key={qualifier}>{qualifier}</span>)}
    </span>
  )
  const body = item.result?.text?.trim()
  if (!body) return <div className={`tool ${item.result?.success === false ? 'failed' : ''}`}><div className="collapsible-head static">{head}</div></div>
  return (
    <Collapsible className={`tool ${item.result?.success === false ? 'failed' : ''}`} summary={head} defaultOpen={item.result?.success === false}>
      <pre className="tool-output">{body}</pre>
    </Collapsible>
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

export const DiffView = memo(function DiffView({ item, onOpenFile }: { item: ItemOf<'diff'>; onOpenFile?: (path: string) => void }) {
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

function AnsiText({ text }: { text: string }) {
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
  const lines = item.text.split('\n').length
  const body = <pre className="output ansi">{item.ansi !== null ? <AnsiText text={item.ansi} /> : item.text}</pre>
  if (lines <= LONG_LINES) {
    return <div className="output-wrap">{body}<CopyButton text={item.text} label="Copy output" /></div>
  }
  return (
    <Collapsible
      className="output-fold"
      summary={<><Terminal size={13} /><span className="muted">{lines} lines of output</span></>}
    >
      <div className="output-wrap">{body}<CopyButton text={item.text} label="Copy output" /></div>
    </Collapsible>
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
        <span><Coins size={12} />↑{compactNumber(round.input_tokens)} ↓{compactNumber(round.output_tokens)}</span>
        {round.input_tokens > 0 ? <span className="muted">{Math.round(hit * 100)}% cached</span> : null}
        {round.tool_calls ? <span><Wrench size={12} />{round.tool_calls}{round.tool_failures ? <em className="fail"> ·{round.tool_failures}✕</em> : null}</span> : null}
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
  const tail = live ? items.slice(-2) : []
  return (
    <div className={`activity ${open ? 'open' : ''} ${live ? 'live' : ''}`}>
      <button type="button" className="activity-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} className="chev" />
        {live ? <Spinner size={13} /> : <Layers size={14} />}
        <span className="activity-count">{summary.tools} tool calls</span>
        <span className="activity-names">
          {summary.names.slice(0, 4).map(([name, count]) => (
            <span key={name} className="pill-mini">{name}{count > 1 ? ` ×${count}` : ''}</span>
          ))}
        </span>
        {summary.failed ? <span className="fail pill-mini">{summary.failed} failed</span> : null}
      </button>
      {open
        ? <div className="activity-body">{items.map((item) => <div key={item.key} data-key={item.key}>{render(item, live)}</div>)}</div>
        : tail.length
          ? <div className="activity-body tail">{tail.map((item) => <div key={item.key} data-key={item.key}>{render(item, live)}</div>)}</div>
          : null}
    </div>
  )
}, sameGroup)
