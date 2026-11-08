import type {
  ConversationSummary,
  DirListing,
  FilePreview,
  FsListing,
  GitChanges,
  NewSessionRequest,
  SessionOptions,
  SessionSummary,
} from './protocol'

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Shared CSRF token, refreshed from every control/state frame. */
let csrfToken = ''
export const setCsrf = (token: string) => {
  if (token) csrfToken = token
}
export const getCsrf = () => csrfToken

const REQUEST_TIMEOUT = 20_000

async function request<T>(path: string, init: RequestInit = {}, timeout = REQUEST_TIMEOUT): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeout)
  let response: Response
  try {
    response = await fetch(path, { credentials: 'same-origin', ...init, signal: controller.signal })
  } catch (exc) {
    throw new ApiError(
      (exc as Error)?.name === 'AbortError' ? 'The request timed out.' : 'Network error. Check the connection.',
      0,
    )
  } finally {
    window.clearTimeout(timer)
  }
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Non-JSON error bodies fall back to the status text below.
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

function get<T>(path: string, params: Record<string, string> = {}) {
  const query = new URLSearchParams(params).toString()
  return request<T>(query ? `${path}?${query}` : path)
}

function post<T>(path: string, body?: unknown, timeout?: number) {
  return request<T>(
    path,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-csrf-token': csrfToken },
      body: JSON.stringify(body ?? {}),
    },
    timeout,
  )
}

const sid = (id: string) => `/api/sessions/${encodeURIComponent(id)}`

export const login = (token: string) =>
  post<{ ok: boolean; csrf: string }>('/api/login', { token }).then((data) => {
    setCsrf(data.csrf)
    return data
  })
export const logout = () => post<{ ok: boolean }>('/api/logout')

// Starting a worker can take a while (model probing), so allow longer.
export const createSession = (body: NewSessionRequest) =>
  post<{ session: SessionSummary }>('/api/sessions', body, 120_000).then((data) => data.session)
export const attachSession = (id: string) =>
  post<{ session: SessionSummary }>(`${sid(id)}/attach`, undefined, 60_000).then((data) => data.session)
export const detachSession = (id: string) => post<{ ok: boolean }>(`${sid(id)}/detach`, undefined, 60_000)
export const endSession = (id: string) => post<{ ok: boolean }>(`${sid(id)}/end`, undefined, 60_000)

export const listFiles = (id: string, path: string) => get<DirListing>(`${sid(id)}/files`, { path })
export const readFile = (id: string, path: string) => get<FilePreview>(`${sid(id)}/file`, { path })
export const rawFileUrl = (id: string, path: string) =>
  `${sid(id)}/raw?${new URLSearchParams({ path }).toString()}`
export const listChanges = (id: string) => get<GitChanges>(`${sid(id)}/changes`)
export const pathCompletions = (id: string, query: string) =>
  get<{ completions: string[] }>(`${sid(id)}/path-completions`, { query }).then((data) => data.completions)

export const browseDirectories = (path: string) => get<FsListing>('/api/fs', path ? { path } : {})
export const recentWorkspaces = () =>
  get<{ workspaces: string[] }>('/api/workspaces').then((data) => data.workspaces)
export const sessionOptions = (cwd: string) => get<SessionOptions>('/api/options', cwd ? { cwd } : {})
export const conversations = (cwd: string) =>
  get<{ conversations: ConversationSummary[] }>('/api/conversations', cwd ? { cwd } : {}).then(
    (data) => data.conversations,
  )

export function errorMessage(exc: unknown, fallback = 'The action failed.') {
  return exc instanceof ApiError ? exc.message : fallback
}
