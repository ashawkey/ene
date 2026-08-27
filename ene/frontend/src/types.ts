export type EventData = {
  text?: string
  path?: string
  old_text?: string
  new_text?: string
  line_num?: number | null
  count?: number
  success?: boolean
  streaming?: boolean
  id?: string
  context_tokens?: number
  context_limit?: number
  input_tokens?: number
  output_tokens?: number
  cached_tokens?: number
  label?: string
  progress?: boolean
  name?: string
  primary?: string
  qualifiers?: string[]
  [key: string]: unknown
}

export type PendingMessage = {
  id: string
  text: string
  source: string
  action_id?: string | null
}

export type Prompt = {
  id: string
  kind: 'select' | 'text'
  message: string
  choices: string[]
  default: string
}

export type ProcessActivity = {
  process_id: string
  label: string
  elapsed_seconds: number
  last_line: string
}

export type ProcessStatus = {
  running: number
  finished: number
  processes: ProcessActivity[]
}

export type StateMessage = {
  type: 'state'
  csrf: string
  session: string
  stream_id: string
  latest_seq: number
  oldest_seq: number
  replay_truncated: boolean
  operation_id: string | null
  process_status: string
  processes?: ProcessStatus
  context_status: Record<string, unknown> | null
  active_indicator: EventData | null
  commands: Record<string, string>
  prompt: Prompt | null
  pending: PendingMessage | null
}

export type SessionSummary = {
  id: string
  title: string
  name: string
  cwd: string
  model: string
  host: string
  conversation_id: string
  /** Last user message, shown when the session has no name. */
  preview: string
  /** 'web' when this hub owns the session, 'terminal' when a terminal does, '' when free. */
  attached_by: string
  state: string
}

export type FsEntry = {
  name: string
  path: string
  hidden: boolean
}

export type FsListing = {
  path: string
  parent: string
  entries: FsEntry[]
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

export type SessionsMessage = {
  type: 'sessions'
  csrf?: string
  sessions: SessionSummary[]
}

export type AgentEvent = {
  type: string
  seq?: number
  data?: EventData
  error?: string
  ok?: boolean
  action_id?: string
  csrf?: string
  session?: string
  sessions?: SessionSummary[]
  stream_id?: string
  latest_seq?: number
  operation_id?: string | null
  prompt?: Prompt | null
  pending?: PendingMessage | null
}

export function isPrompt(value: unknown): value is Prompt {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.id === 'string' &&
    (candidate.kind === 'select' || candidate.kind === 'text') &&
    typeof candidate.message === 'string' &&
    Array.isArray(candidate.choices) &&
    typeof candidate.default === 'string'
  )
}

export const displayTypes = new Set([
  'assistant_message',
  'user_message',
  'system',
  'warning',
  'error',
  'tool_start',
  'tool_result',
  'output',
  'debug',
  'diff',
  'thinking',
])

export type DisplayEvent = {
  key: string
  type: string
  text: string
  data: EventData
}

export type ClientAction =
  | { type: 'submit'; text: string; action_id: string }
  | { type: 'withdraw_pending'; id: string; action_id: string }
  | { type: 'prompt_response'; id: string; answer: string }
  | { type: 'cancel'; operation_id: string | null }
