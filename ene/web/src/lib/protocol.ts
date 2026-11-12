/** Wire types shared with the hub (`ene/hub/server.py`). */

export type SessionState = 'working' | 'waiting' | 'done' | 'starting' | string

export type SessionSummary = {
  id: string
  title: string
  name: string
  cwd: string
  model: string
  host: string
  conversation_id: string
  preview: string
  /** 'web' when this hub owns the session, 'terminal' when a terminal does, '' when free. */
  attached_by: string
  state: SessionState
  watchers?: number
  background?: number
}

export type Prompt = {
  id: string
  kind: 'select' | 'text'
  message: string
  choices: string[]
  default: string
}

export type PendingMessage = {
  id: string
  text: string
  source: string
  action_id?: string | null
  appendable?: boolean
  /** False for `/wait` messages, which run after the current round instead of joining it. */
  steer?: boolean
  /** Epoch seconds when a delayed `/wait` message becomes ready. */
  ready_at?: number | null
}

export type ProcessActivity = {
  process_id: string
  label: string
  /** Shell command, whitespace-collapsed and clipped. */
  command?: string
  elapsed_seconds: number
  last_line: string
  watching?: boolean
  status?: 'running' | 'exited'
  exit_code?: number | null
  /** Bytes captured in the log so far; viewers fetch only what they lack. */
  log_bytes?: number
  /** Bytes of structured agent events; non-zero for a subagent. */
  events_bytes?: number
  kind?: 'shell' | 'agent'
}

/** One byte range of a process log (exact) or event file (whole lines). */
export type ProcessOutput = {
  start: number
  end: number
  size: number
  status: 'running' | 'exited'
  text?: string
  events?: ServerEvent[]
}

export type ProcessSnapshot = {
  running: number
  finished: number
  processes: ProcessActivity[]
}

export type ContextSnapshot = {
  context_tokens: number
  context_limit: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  model?: string
  model_icon?: string
}

export type Indicator = {
  label?: string
  suffix?: string
  started_at?: number
  progress?: boolean
  countdown?: number | null
  round_elapsed?: number | null
  context_tokens?: number
  context_limit?: number
  input_tokens?: number
  output_tokens?: number
  cached_tokens?: number
  /** Live output tokens/s estimate while a model response streams. */
  tps?: number | null
}

/** One agent round, recorded and persisted by the agent (ene/backend/rounds.py). */
export type RoundStats = {
  id: string
  index: number
  /** 'user', 'notification', or 'continue'. */
  kind: string
  model: string
  prompt: string
  started_at: number
  ended_at: number | null
  duration: number
  /** Event seq where the round began in this view; 0 for rounds from history. */
  start_seq: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  context_tokens: number
  context_limit: number
  tool_calls: number
  tool_failures: number
  edits: number
  cancelled: boolean
  model_calls: number
  /** Seconds spent waiting on model requests. */
  model_time: number
  /** Seconds spent running tool calls. */
  tool_time: number
  /** Mean time to first token over the round's streamed requests. */
  ttft: number | null
  /** Output tokens per second while generating. */
  tps: number | null
}

export type StateFrame = {
  type: 'state'
  csrf: string
  session: string
  stream_id: string
  latest_seq: number
  /** First seq the server streams after this frame. */
  replay_from: number
  /** Seq to request older history before, or null when none is retained. */
  history_before: number | null
  /** Full detail starts here; older history is condensed to messages. */
  detail_from: number | null
  operation_id: string | null
  process_status: string
  processes?: ProcessSnapshot
  context_status: ContextSnapshot | null
  active_indicator: Indicator | null
  commands: Record<string, string>
  prompt: Prompt | null
  pending: PendingMessage | null
  info?: Record<string, string>
  rounds?: RoundStats[]
  active_round?: { started_at: number } | null
}

/** One page of older events, answering a `history` request. */
export type HistoryFrame = {
  type: 'history'
  session: string
  stream_id: string
  before: number
  events: ServerEvent[]
  history_before: number | null
  detail_from: number | null
}

export type ServerEvent = {
  type: string
  seq?: number
  timestamp?: number
  data?: Record<string, unknown>
  // Acknowledgements.
  ok?: boolean
  error?: string
  action_id?: string
  duplicate?: boolean
}

export type ClientAction =
  | { type: 'submit'; text: string; action_id: string }
  | { type: 'withdraw_pending'; id: string; action_id: string }
  | { type: 'prompt_response'; id: string; answer: string }
  | { type: 'prompt_cancel'; id: string }
  | { type: 'cancel'; operation_id: string | null }
  | { type: 'history'; before: number }

export type DirEntry = {
  name: string
  type: 'dir' | 'file'
  size: number
  mtime: number
  hidden: boolean
  symlink: boolean
}

export type DirListing = {
  root: string
  path: string
  entries: DirEntry[]
  truncated: boolean
}

export type FilePreview = {
  path: string
  name: string
  size: number
  mtime: number
  kind: 'text' | 'image' | 'binary'
  mime?: string
  text?: string
  truncated?: boolean
}

export type GitChange = {
  path: string
  status: 'modified' | 'added' | 'deleted' | 'renamed' | 'copied' | 'conflict' | 'untracked'
  staged: boolean
}

export type GitBranch = {
  git: boolean
  branch: string
  detached: boolean
}

export type FoundPath = { path: string; type: 'dir' | 'file' }

export type FindResult = { results: FoundPath[]; truncated: boolean }

export type SearchMatch = {
  line: number
  /** Preview of the line; long lines are clipped around the first match. */
  text: string
  /** Character [start, end) ranges of matches within ``text``. */
  ranges: Array<[number, number]>
  clipped: boolean
}

export type SearchResult = {
  root: string
  /** ``capped``: the file has more matching lines than ``per_file_limit``; only those were read. */
  files: Array<{ path: string; matches: SearchMatch[]; capped: boolean }>
  match_count: number
  file_count: number
  truncated: boolean
  reason: 'limit' | 'timeout' | 'cancelled' | null
  per_file_limit: number
}

export type SearchOptions = { regex: boolean; case: boolean; word: boolean; include: string; exclude: string }

export type GitChanges = {
  git: boolean
  branch: string
  changes: GitChange[]
  /** Set when the folder is a repo but `git status` failed or timed out. */
  error?: string
}

export type GitDiff = {
  path: string
  status: 'added' | 'deleted' | 'modified' | 'unchanged'
  old: string
  new: string
  binary: boolean
  too_large: boolean
}

export type FsListing = {
  path: string
  parent: string
  entries: { name: string; path: string; hidden: boolean }[]
}

export type SessionOptions = {
  models: string[]
  default_model: string
  personas: string[]
  reasoning_efforts: string[]
}

export type ConversationSummary = {
  id: string
  name: string
  message_count: number | string
  round_id: number | string
  last_user_message: string
  live: boolean
}

export type NewSessionRequest = {
  cwd: string
  name: string
  model: string
  persona: string
  reasoning_effort: string
  resume: string
}

export function isPrompt(value: unknown): value is Prompt {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.id === 'string' &&
    (v.kind === 'select' || v.kind === 'text') &&
    typeof v.message === 'string' &&
    Array.isArray(v.choices) &&
    typeof v.default === 'string'
  )
}

export type TerminalInfo = {
  id: string
  title: string
  shell: string
  alive: boolean
  exit_code: number | null
  created: number
}

export type TerminalListing = {
  supported: boolean
  terminals: TerminalInfo[]
}
