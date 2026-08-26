import type {
  ConversationSummary,
  FsListing,
  NewSessionRequest,
  SessionOptions,
  SessionSummary,
} from './types'

/** Error carrying the hub's message for a failed request. */
export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function parse<T>(response: Response): Promise<T> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // A non-JSON body is reported through the status text below.
  }
  if (!response.ok) {
    const detail =
      body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string'
        ? (body as { detail: string }).detail
        : response.statusText || 'Request failed'
    throw new ApiError(detail, response.status)
  }
  return body as T
}

async function get<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString()
  const response = await fetch(query ? `${path}?${query}` : path)
  return parse<T>(response)
}

async function post<T>(path: string, csrf: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-csrf-token': csrf },
    body: JSON.stringify(body ?? {}),
  })
  return parse<T>(response)
}

export const listDirectory = (path: string) => get<FsListing>('/api/fs', path ? { path } : {})

export const listWorkspaces = () =>
  get<{ workspaces: string[] }>('/api/workspaces').then((data) => data.workspaces)

export const listOptions = (cwd: string) => get<SessionOptions>('/api/options', cwd ? { cwd } : {})

export const listConversations = (cwd: string) =>
  get<{ conversations: ConversationSummary[] }>(
    '/api/conversations',
    cwd ? { cwd } : {},
  ).then((data) => data.conversations)

export const createSession = (csrf: string, request: NewSessionRequest) =>
  post<{ session: SessionSummary }>('/api/sessions', csrf, request).then((data) => data.session)

export const attachSession = (csrf: string, id: string) =>
  post<{ session: SessionSummary }>(`/api/sessions/${encodeURIComponent(id)}/attach`, csrf).then(
    (data) => data.session,
  )

export const detachSession = (csrf: string, id: string) =>
  post<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(id)}/detach`, csrf)
