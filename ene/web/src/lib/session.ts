import type {
  ContextSnapshot,
  HistoryFrame,
  Indicator,
  PendingMessage,
  ProcessSnapshot,
  Prompt,
  RoundStats,
  ServerEvent,
  StateFrame,
} from './protocol'
import { isPrompt } from './protocol'

type Base = { key: string; seq: number; ts: number }
/**
 * When a message was sent (user) or started (reply), from its `created_at`.
 * Null for messages saved before timestamps existed; absent means the event
 * time (`ts`) applies.
 */
type Sent = { at?: number | null }

/** An image a tool loaded (`read_image`); `path` is the agent's local path. */
export type ToolImage = { path: string; mime: string }
export type ToolResult = { text: string; success: boolean; image?: ToolImage }
export type OutputBlock = { text: string; ansi: string | null }
export type DiffData = {
  path: string
  oldText: string
  newText: string
  line: number | null
  count: number
  success: boolean
}

export type TimelineItem =
  | (Base & Sent & { kind: 'user'; text: string; source: string; steer: boolean })
  | (Base & Sent & { kind: 'assistant'; text: string; streaming: boolean })
  | (Base & { kind: 'thinking'; text: string; streaming: boolean })
  | (Base & {
      kind: 'tool'
      name: string
      primary: string
      qualifiers: string[]
      text: string
      result: ToolResult | null
      /** Command output streamed while the call ran. */
      output: OutputBlock | null
      /** File edits the call made (edit tools report these instead of a result). */
      diffs: DiffData[]
    })
  | (Base & { kind: 'diff' } & DiffData)
  | (Base & { kind: 'output' } & OutputBlock)
  | (Base & { kind: 'notice'; level: 'system' | 'debug' | 'warning' | 'error'; text: string })
  | (Base & { kind: 'round'; round: RoundStats })

export type LiveIndicator = Indicator & { receivedAt: number }
export type LiveProcesses = ProcessSnapshot & { receivedAt: number }

export type SessionView = {
  streamKey: string
  lastSeq: number
  items: TimelineItem[]
  operationId: string | null
  operationSince: number | null
  prompt: Prompt | null
  pending: PendingMessage | null
  indicator: LiveIndicator | null
  context: ContextSnapshot | null
  processes: LiveProcesses
  commands: Record<string, string>
  info: Record<string, string>
  rounds: RoundStats[]
  history: string[]
  /** Seq to request older events before, or null when nothing older is retained. */
  historyBefore: number | null
  /**
   * Seq from which full detail is retained; older history shows only the
   * direct conversation. Null when nothing was evicted.
   */
  detailFrom: number | null
  localCounter: number
  /** Seq of the latest operation_start, to anchor the next round summary. */
  roundStartSeq: number
}

/** Side effects the reducer reports for the connection layer to act on. */
export type Signal =
  | { kind: 'state'; pendingActionId: string | null; streamChanged: boolean }
  | { kind: 'ack'; actionId: string }
  | { kind: 'reject'; actionId: string; error: string }
  | { kind: 'withdrawn'; actionId: string; text: string }
  | { kind: 'withdraw-failed'; actionId: string; error: string }
  | { kind: 'draft'; text: string }
  | { kind: 'error'; text: string }

export const emptyProcesses = (): LiveProcesses => ({
  running: 0,
  finished: 0,
  processes: [],
  receivedAt: Date.now(),
})

export function initialView(): SessionView {
  return {
    streamKey: '',
    lastSeq: 0,
    items: [],
    operationId: null,
    operationSince: null,
    prompt: null,
    pending: null,
    indicator: null,
    context: null,
    processes: emptyProcesses(),
    commands: {},
    info: {},
    rounds: [],
    history: [],
    historyBefore: null,
    detailFrom: null,
    localCounter: 0,
    roundStartSeq: 0,
  }
}

const str = (value: unknown, fallback = '') => (typeof value === 'string' ? value : fallback)
const optionalNum = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
const num = (value: unknown, fallback = 0) =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback

/**
 * A message's own time: `created_at` when the event carries it (null for a
 * message saved without one), else *fallback* for workers that predate it.
 */
function sentAt(data: Record<string, unknown>, fallback: number | null): number | null {
  if (!('created_at' in data)) return fallback
  return optionalNum(data.created_at)
}

/** The time to show beside a message, or null when it has none. */
export function messageTime(item: { ts: number; at?: number | null }): number | null {
  return item.at === undefined ? item.ts : item.at
}

export function parseContext(data: Record<string, unknown> | null | undefined): ContextSnapshot | null {
  if (!data || typeof data.context_tokens !== 'number') return null
  return {
    context_tokens: num(data.context_tokens),
    context_limit: num(data.context_limit),
    input_tokens: num(data.input_tokens),
    output_tokens: num(data.output_tokens),
    cached_tokens: num(data.cached_tokens),
    model: typeof data.model === 'string' ? data.model : undefined,
    model_icon: typeof data.model_icon === 'string' ? data.model_icon : undefined,
  }
}

export function parseProcesses(data: Record<string, unknown> | null | undefined): LiveProcesses {
  const list = Array.isArray(data?.processes) ? data.processes : []
  const processes = list.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const p = item as Record<string, unknown>
    if (typeof p.process_id !== 'string' || typeof p.label !== 'string') return []
    const eventsBytes = num(p.events_bytes)
    return [{
      process_id: p.process_id,
      label: p.label,
      command: str(p.command),
      elapsed_seconds: num(p.elapsed_seconds),
      last_line: str(p.last_line),
      watching: p.watching === true,
      status: p.status === 'exited' ? 'exited' as const : 'running' as const,
      exit_code: typeof p.exit_code === 'number' ? p.exit_code : null,
      log_bytes: num(p.log_bytes),
      events_bytes: eventsBytes,
      kind: p.kind === 'agent' || eventsBytes > 0 ? 'agent' as const : 'shell' as const,
    }]
  })
  return {
    running: num(data?.running, processes.filter((item) => item.status === 'running').length),
    finished: num(data?.finished),
    processes,
    receivedAt: Date.now(),
  }
}

function parseRound(data: Record<string, unknown>, startSeq = 0): RoundStats | null {
  if (typeof data.id !== 'string') return null
  return {
    id: data.id,
    index: num(data.index),
    kind: str(data.kind, 'user'),
    model: str(data.model),
    prompt: str(data.prompt),
    started_at: num(data.started_at),
    ended_at: typeof data.ended_at === 'number' ? data.ended_at : null,
    duration: num(data.duration),
    start_seq: num(data.start_seq, startSeq),
    input_tokens: num(data.input_tokens),
    output_tokens: num(data.output_tokens),
    cached_tokens: num(data.cached_tokens),
    reasoning_tokens: num(data.reasoning_tokens),
    context_tokens: num(data.context_tokens),
    context_limit: num(data.context_limit),
    tool_calls: num(data.tool_calls),
    tool_failures: num(data.tool_failures),
    edits: num(data.edits),
    cancelled: data.cancelled === true,
    model_calls: num(data.model_calls),
    model_time: num(data.model_time),
    tool_time: num(data.tool_time),
    ttft: optionalNum(data.ttft),
    tps: optionalNum(data.tps),
  }
}

function upsertRound(rounds: RoundStats[], round: RoundStats): RoundStats[] {
  const index = rounds.findIndex((item) => item.id === round.id)
  if (index < 0) return [...rounds, round].slice(-500)
  const next = rounds.slice()
  next[index] = { ...round, start_seq: round.start_seq || rounds[index].start_seq }
  return next
}

function parseRounds(value: unknown, previous: RoundStats[]): RoundStats[] {
  if (!Array.isArray(value)) return previous
  // Keep anchors already learned from the live stream.
  const anchors = new Map(previous.map((round) => [round.id, round.start_seq]))
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object') return []
    const round = parseRound(item as Record<string, unknown>, anchors.get((item as { id?: string }).id ?? '') ?? 0)
    return round ? [round] : []
  }).slice(-500)
}

/** Index of the open streaming item of *kind* in the current tail, or -1. */
function openStream(items: TimelineItem[], kind: 'assistant' | 'thinking') {
  for (let i = items.length - 1; i >= 0 && i >= items.length - 6; i--) {
    const item = items[i]
    if ((item.kind === 'assistant' || item.kind === 'thinking') && item.streaming) {
      if (item.kind === kind) return i
      continue
    }
    break
  }
  return -1
}

function append(items: TimelineItem[], item: TimelineItem): TimelineItem[] {
  const last = items.at(-1)
  // Consecutive command output and system notices read as one block.
  if (last && last.kind === 'output' && item.kind === 'output') {
    return [...items.slice(0, -1), { ...last, ...mergeOutput(last, item) }]
  }
  if (last && last.kind === 'notice' && item.kind === 'notice' && last.level === item.level && item.level === 'system') {
    return [...items.slice(0, -1), { ...last, text: `${last.text}\n${item.text}` }]
  }
  return [...items, item]
}

function toolImage(value: unknown): ToolImage | null {
  if (!value || typeof value !== 'object') return null
  const { path, mime } = value as Record<string, unknown>
  return typeof path === 'string' && path ? { path, mime: typeof mime === 'string' ? mime : '' } : null
}

type ToolItem = Extract<TimelineItem, { kind: 'tool' }>

/** Index of the oldest unanswered call in the current run, or -1. */
function openTool(items: TimelineItem[]) {
  let target = -1
  for (let i = items.length - 1; i >= 0 && i >= items.length - 40; i--) {
    const item = items[i]
    if (item.kind === 'user' || item.kind === 'round') break
    if (item.kind === 'tool' && item.result === null) target = i
  }
  return target
}

function updateTool(items: TimelineItem[], index: number, update: (tool: ToolItem) => Partial<ToolItem>) {
  const next = items.slice()
  const tool = items[index] as ToolItem
  next[index] = { ...tool, ...update(tool) }
  return next
}

/** Pair a tool result with the oldest unanswered call in the current run. */
function attachResult(items: TimelineItem[], result: ToolResult): TimelineItem[] | null {
  const target = openTool(items)
  return target < 0 ? null : updateTool(items, target, () => ({ result }))
}

/** Command output printed while a call runs belongs to that call. */
function attachOutput(items: TimelineItem[], block: OutputBlock): TimelineItem[] | null {
  const target = openTool(items)
  if (target < 0) return null
  return updateTool(items, target, (tool) => ({
    output: tool.output ? mergeOutput(tool.output, block) : block,
  }))
}

/**
 * Edit tools answer with diffs instead of a result. The first diff settles
 * the open call; further diffs from the same call (multi_edit) follow it.
 */
function attachDiff(items: TimelineItem[], diff: DiffData): TimelineItem[] | null {
  let target = openTool(items)
  if (target < 0) {
    const last = items.length - 1
    const tool = items[last]
    if (tool?.kind === 'tool' && tool.diffs.length) target = last
  }
  if (target < 0) return null
  return updateTool(items, target, (tool) => ({
    diffs: [...tool.diffs, diff],
    result: { text: tool.result?.text ?? '', success: (tool.result?.success ?? true) && diff.success },
  }))
}

function mergeOutput(a: OutputBlock, b: OutputBlock): OutputBlock {
  const ansi = a.ansi !== null || b.ansi !== null ? `${a.ansi ?? a.text}\n${b.ansi ?? b.text}` : null
  return { text: `${a.text}\n${b.text}`, ansi }
}

function indicatorFrom(data: Record<string, unknown>): LiveIndicator {
  return {
    label: str(data.label, 'Working'),
    suffix: str(data.suffix),
    started_at: typeof data.started_at === 'number' ? data.started_at : undefined,
    progress: data.progress === true,
    countdown: typeof data.countdown === 'number' ? data.countdown : null,
    round_elapsed: typeof data.round_elapsed === 'number' ? data.round_elapsed : null,
    context_tokens: num(data.context_tokens),
    context_limit: num(data.context_limit),
    input_tokens: num(data.input_tokens),
    output_tokens: num(data.output_tokens),
    cached_tokens: num(data.cached_tokens),
    tps: optionalNum(data.tps),
    ttft: optionalNum(data.ttft),
    receivedAt: Date.now(),
  }
}

export function applyState(view: SessionView, frame: StateFrame): [SessionView, Signal[]] {
  const key = `${frame.session}:${frame.stream_id}`
  const streamChanged = Boolean(view.streamKey && view.streamKey !== key)
  const replayFrom = typeof frame.replay_from === 'number' ? frame.replay_from : 1
  // The server resumes at the client's cursor on a plain reconnect. Otherwise
  // (a fresh load, a new stream, or a cursor that aged out of retained
  // history) it starts at the newest page; older pages load on demand.
  const restart = streamChanged || view.lastSeq === 0 || replayFrom > view.lastSeq + 1
  const base: SessionView = restart
    ? {
        ...initialView(),
        history: view.history,
        lastSeq: Math.max(0, replayFrom - 1),
        historyBefore: frame.history_before ?? null,
        detailFrom: frame.detail_from ?? null,
      }
    : view
  const rounds = parseRounds(frame.rounds, base.rounds)
  const next: SessionView = {
    ...base,
    streamKey: key,
    operationId: frame.operation_id,
    operationSince:
      frame.operation_id === null
        ? null
        : frame.active_round?.started_at ?? base.operationSince ?? Date.now() / 1000,
    prompt: isPrompt(frame.prompt) ? frame.prompt : null,
    pending: frame.pending,
    indicator:
      frame.operation_id !== null && frame.active_indicator
        ? indicatorFrom(frame.active_indicator as Record<string, unknown>)
        : null,
    context: parseContext(frame.context_status as Record<string, unknown> | null) ?? base.context,
    processes: parseProcesses(frame.processes as unknown as Record<string, unknown>),
    commands: frame.commands ?? {},
    info: frame.info ?? base.info,
    rounds,
  }
  return [next, [{ kind: 'state', pendingActionId: frame.pending?.action_id ?? null, streamChanged }]]
}

/**
 * Prepend one page of older events. Pages start at a user message, so folding
 * one on its own pairs every tool call with its result.
 */
export function applyHistory(view: SessionView, frame: HistoryFrame): SessionView {
  // Stale answers (another stream, or a page already applied) are dropped.
  if (view.streamKey !== `${frame.session}:${frame.stream_id}` || frame.before !== view.historyBefore) {
    return view
  }
  let page = initialView()
  for (const event of Array.isArray(frame.events) ? frame.events : []) {
    page = reduce(page, event)[0]
  }
  const known = new Set(view.items.map((item) => item.key))
  const older = page.items
    .filter((item) => !known.has(item.key))
    .map((item) => (item.kind === 'assistant' || item.kind === 'thinking') && item.streaming
      ? { ...item, streaming: false }
      : item)
  // Older pages carry the start anchors of rounds the state frame listed.
  const anchors = new Map(page.rounds.filter((round) => round.start_seq).map((round) => [round.id, round.start_seq]))
  const rounds = anchors.size
    ? view.rounds.map((round) => round.start_seq ? round : { ...round, start_seq: anchors.get(round.id) ?? 0 })
    : view.rounds
  return {
    ...view,
    items: older.length ? [...older, ...view.items] : view.items,
    rounds,
    history: [...page.history.filter((text) => !view.history.includes(text)), ...view.history].slice(-200),
    historyBefore: frame.history_before ?? null,
    detailFrom: frame.detail_from ?? null,
  }
}

/** Fold one server frame into the view. Pure: same input, same output. */
export function reduce(view: SessionView, message: ServerEvent): [SessionView, Signal[]] {
  if (message.type === 'state') return applyState(view, message as unknown as StateFrame)
  if (message.type === 'history') return [applyHistory(view, message as unknown as HistoryFrame), []]

  // Acknowledgements are not part of the ordered event stream.
  if (message.type === 'accepted') {
    return [view, message.action_id ? [{ kind: 'ack', actionId: message.action_id }] : []]
  }
  if (message.type === 'rejected') {
    const error = message.error || 'The message was rejected.'
    return [view, message.action_id
      ? [{ kind: 'reject', actionId: message.action_id, error }]
      : [{ kind: 'error', text: error }]]
  }
  if (message.type === 'cancel_ack') {
    return [message.ok === false ? { ...view, operationId: null } : view, []]
  }
  if (message.type === 'prompt_ack') {
    return [view, message.ok === false ? [{ kind: 'error', text: 'The answer could not be delivered.' }] : []]
  }

  const seq = typeof message.seq === 'number' ? message.seq : 0
  if (seq) {
    // Replays after reconnect can overlap what we already have.
    if (seq <= view.lastSeq) return [view, []]
  }
  const data = (message.data ?? {}) as Record<string, unknown>
  const ts = typeof message.timestamp === 'number' ? message.timestamp : Date.now() / 1000
  let localCounter = view.localCounter
  const key = seq ? `e${seq}` : `l${++localCounter}`
  let next: SessionView = { ...view, lastSeq: seq || view.lastSeq, localCounter }
  const signals: Signal[] = []
  const items = next.items

  switch (message.type) {
    case 'user_message': {
      const text = str(data.text)
      const source = str(data.source)
      next.items = append(items, {
        kind: 'user', key, seq, ts, at: sentAt(data, ts), text, source, steer: data.steer === true,
      })
      if (text && source !== 'inbox' && source !== 'replay') {
        next.history = [...next.history.filter((entry) => entry !== text), text].slice(-200)
      } else if (text && source === 'replay' && !next.history.includes(text)) {
        next.history = [...next.history, text].slice(-200)
      }
      break
    }
    case 'assistant_delta':
    case 'thinking_delta': {
      const kind = message.type === 'assistant_delta' ? 'assistant' : 'thinking'
      const index = openStream(items, kind)
      const text = str(data.text)
      if (index >= 0) {
        const target = items[index] as Extract<TimelineItem, { kind: 'assistant' | 'thinking' }>
        const copy = items.slice()
        copy[index] = { ...target, text: target.text + text }
        next.items = copy
      } else {
        // A reply is stamped when its first text arrives, like the agent does.
        const item = kind === 'assistant'
          ? { kind, key: `s${key}`, seq, ts, at: ts, text, streaming: true }
          : { kind, key: `s${key}`, seq, ts, text, streaming: true }
        next.items = [...items, item as TimelineItem]
      }
      break
    }
    case 'assistant_message':
    case 'thinking': {
      const kind = message.type === 'assistant_message' ? 'assistant' : 'thinking'
      const index = openStream(items, kind)
      const item = { kind, key, seq, ts, text: str(data.text), streaming: false } as TimelineItem
      if (item.kind === 'assistant') {
        const streamed = index >= 0 ? (items[index] as { at?: number | null }).at : undefined
        item.at = sentAt(data, streamed ?? ts)
      }
      if (index >= 0) {
        const copy = items.slice()
        copy[index] = { ...item, key: items[index].key }
        next.items = copy
      } else {
        next.items = [...items, item]
      }
      break
    }
    case 'tool_start':
      next.items = [...items, {
        kind: 'tool', key, seq, ts,
        name: str(data.name),
        primary: str(data.primary),
        qualifiers: Array.isArray(data.qualifiers) ? data.qualifiers.filter((q): q is string => typeof q === 'string') : [],
        text: str(data.text),
        result: null,
        output: null,
        diffs: [],
      }]
      break
    case 'tool_result': {
      const result: ToolResult = { text: str(data.text), success: data.success !== false }
      const image = toolImage(data.image)
      if (image) result.image = image
      next.items = attachResult(items, result) ?? [...items, {
        kind: 'tool', key, seq, ts, name: '', primary: '', qualifiers: [], text: '', result, output: null, diffs: [],
      }]
      break
    }
    case 'diff': {
      const diff: DiffData = {
        path: str(data.path),
        oldText: str(data.old_text),
        newText: str(data.new_text),
        line: typeof data.line_num === 'number' ? data.line_num : null,
        count: num(data.count, 1),
        success: data.success !== false,
      }
      next.items = attachDiff(items, diff) ?? [...items, { kind: 'diff', key, seq, ts, ...diff }]
      break
    }
    case 'output': {
      // Prefer the wide form (rows not clipped to a terminal width); this
      // view wraps lines itself.
      const ansi = typeof data.wide_ansi === 'string' ? data.wide_ansi : data.ansi
      const block: OutputBlock = { text: str(data.wide_text, str(data.text)), ansi: typeof ansi === 'string' ? ansi : null }
      next.items = attachOutput(items, block) ?? append(items, { kind: 'output', key, seq, ts, ...block })
      break
    }
    case 'system':
    case 'debug':
    case 'warning':
    case 'error':
      next.items = append(items, { kind: 'notice', key, seq, ts, level: message.type, text: str(data.text) })
      break
    case 'round_history':
      next.rounds = parseRounds(data.rounds, next.rounds)
      break
    case 'round_stats': {
      const round = parseRound(data, next.roundStartSeq)
      if (!round) break
      next.rounds = upsertRound(next.rounds, round)
      const index = items.findIndex((item) => item.kind === 'round' && item.round.id === round.id)
      if (index >= 0) {
        const copy = items.slice()
        copy[index] = { ...(items[index] as Extract<TimelineItem, { kind: 'round' }>), round }
        next.items = copy
      } else {
        next.items = [...items, { kind: 'round', key: `r-${round.id}`, seq, ts, round }]
      }
      break
    }
    case 'timeline_reset':
      next.items = []
      next.indicator = null
      next.historyBefore = null
      next.detailFrom = null
      break
    case 'operation_start':
      next.operationId = str(data.id) || null
      next.operationSince = ts
      next.roundStartSeq = seq
      break
    case 'operation_end':
      next.operationId = null
      next.operationSince = null
      next.indicator = null
      // A stream left open by an interrupted reply stops pulsing.
      next.items = items.map((item) =>
        (item.kind === 'assistant' || item.kind === 'thinking') && item.streaming
          ? { ...item, streaming: false }
          : item,
      )
      break
    case 'thinking_start': {
      next.indicator = indicatorFrom(data)
      const context = parseContext(data)
      if (context) next.context = context
      break
    }
    case 'thinking_update':
      if (next.indicator) {
        next.indicator = {
          ...next.indicator,
          suffix: str(data.suffix, next.indicator.suffix),
          tps: optionalNum(data.tps) ?? next.indicator.tps,
          ttft: optionalNum(data.ttft) ?? next.indicator.ttft,
        }
      }
      break
    case 'thinking_stop':
      next.indicator = null
      break
    case 'context_status': {
      const context = parseContext(data)
      if (context) next.context = context
      break
    }
    case 'process_status':
      next.processes = parseProcesses(data)
      break
    case 'commands':
      if (data.commands && typeof data.commands === 'object') {
        next.commands = Object.fromEntries(
          Object.entries(data.commands as Record<string, unknown>).filter(
            (entry): entry is [string, string] => typeof entry[1] === 'string',
          ),
        )
      }
      break
    case 'prompt_open':
      if (isPrompt(data)) next.prompt = data
      break
    case 'prompt_resolved':
      next.prompt = null
      break
    case 'pending_set': {
      const actionId = typeof data.action_id === 'string' ? data.action_id : null
      next.pending = {
        id: str(data.id),
        text: str(data.text),
        source: str(data.source),
        action_id: actionId,
        appendable: data.appendable === true,
        steer: data.steer !== false,
        ready_at: optionalNum(data.ready_at),
      }
      if (actionId) signals.push({ kind: 'ack', actionId })
      break
    }
    case 'pending_cleared':
      if (!next.pending || next.pending.id === data.id) next.pending = null
      if (data.reason === 'withdrawn' && typeof data.action_id === 'string') {
        signals.push({ kind: 'withdrawn', actionId: data.action_id, text: str(data.text) })
      }
      break
    case 'submission_rejected':
      if (typeof data.action_id === 'string' && data.action_id) {
        signals.push({ kind: 'reject', actionId: data.action_id, error: str(data.error, 'Rejected') })
      }
      break
    case 'withdrawal_rejected':
      if (typeof data.action_id === 'string' && data.action_id) {
        signals.push({ kind: 'withdraw-failed', actionId: data.action_id, error: str(data.error) })
      }
      break
    case 'draft_set':
      signals.push({ kind: 'draft', text: str(data.text) })
      break
    case 'session_meta':
      break
    default:
      break
  }
  return [next, signals]
}

/** Context fill as 0..1, or null when the limit is unknown. */
export function contextFraction(context: ContextSnapshot | null): number | null {
  if (!context || context.context_limit <= 0) return null
  return Math.min(1, Math.max(0, context.context_tokens / context.context_limit))
}

export type Totals = {
  rounds: number
  duration: number
  modelTime: number
  toolTime: number
  input: number
  output: number
  cached: number
  tools: number
  failures: number
  edits: number
  /** Mean of the per-round figures, over rounds that measured them. */
  ttft: number | null
  tps: number | null
}

const mean = (values: Array<number | null>) => {
  const known = values.filter((value): value is number => value !== null)
  return known.length ? known.reduce((sum, value) => sum + value, 0) / known.length : null
}

export function totals(rounds: RoundStats[]): Totals {
  const sum = rounds.reduce(
    (acc, round) => ({
      rounds: acc.rounds + 1,
      duration: acc.duration + round.duration,
      modelTime: acc.modelTime + round.model_time,
      toolTime: acc.toolTime + round.tool_time,
      input: acc.input + round.input_tokens,
      output: acc.output + round.output_tokens,
      cached: acc.cached + round.cached_tokens,
      tools: acc.tools + round.tool_calls,
      failures: acc.failures + round.tool_failures,
      edits: acc.edits + round.edits,
    }),
    { rounds: 0, duration: 0, modelTime: 0, toolTime: 0, input: 0, output: 0, cached: 0, tools: 0, failures: 0, edits: 0 },
  )
  return { ...sum, ttft: mean(rounds.map((round) => round.ttft)), tps: mean(rounds.map((round) => round.tps)) }
}
