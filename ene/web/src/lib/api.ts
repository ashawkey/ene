import type {
  ConversationSummary,
  DirListing,
  FilePreview,
  FsListing,
  GitBranch,
  FindResult,
  GitChanges,
  GitDiff,
  GitLog,
  SearchOptions,
  SearchResult,
  NewSessionRequest,
  ProcessOutput,
  SessionOptions,
  SessionSummary,
  TerminalInfo,
  TerminalListing,
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
// The hub allows `git status` and then `git diff --numstat` 60 s each.
const CHANGES_TIMEOUT = 130_000

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

function get<T>(path: string, params: Record<string, string> = {}, timeout?: number) {
  const query = new URLSearchParams(params).toString()
  return request<T>(query ? `${path}?${query}` : path, {}, timeout)
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
/** Which of *paths* name files the viewer can open. */
export const existingFiles = (id: string, paths: string[]) =>
  request<{ files: string[] }>(
    `${sid(id)}/files/exists?${new URLSearchParams(paths.map((path) => ['path', path])).toString()}`,
  ).then((data) => data.files)
export const findFiles = (id: string, query: string) => get<FindResult>(`${sid(id)}/files/find`, { query })
export const searchText = (id: string, query: string, options: SearchOptions) =>
  get<SearchResult>(`${sid(id)}/files/search`, {
    query,
    regex: String(options.regex),
    case: String(options.case),
    word: String(options.word),
    include: options.include,
    exclude: options.exclude,
  })
export const rawFileUrl = (id: string, path: string) =>
  `${sid(id)}/raw?${new URLSearchParams({ path }).toString()}`
export const downloadUrl = (id: string, path: string) =>
  `${sid(id)}/download?${new URLSearchParams({ path }).toString()}`
/** A zip of *paths* (files or folders), streamed as it is built. */
export const zipUrl = (id: string, paths: string[]) =>
  `${sid(id)}/download/zip?${new URLSearchParams(paths.map((path) => ['path', path])).toString()}`
/** What a zip of *paths* would hold; fails with the hub's limit message. */
export const checkZip = (id: string, paths: string[]) =>
  request<{ name: string; files: number; bytes: number }>(`${zipUrl(id, paths)}&check=1`, {}, 60_000)
/** Which of *names* (relative to workspace folder *dir*) an upload would replace. */
export const existingUploads = (id: string, dir: string, names: string[]) =>
  post<{ existing: string[] }>(`${sid(id)}/upload/check`, { dir, names }).then((data) => data.existing)
export const uploadUrl = (id: string, dir: string, name: string, overwrite: boolean) =>
  `${sid(id)}/upload?${new URLSearchParams({ dir, name, overwrite: overwrite ? '1' : '' }).toString()}`
export const attachmentUrl = (id: string, name: string) =>
  `${sid(id)}/attachments?${new URLSearchParams({ name }).toString()}`
export const deleteAttachment = (id: string, path: string) =>
  post<{ ok: boolean }>(`${sid(id)}/attachments/delete`, { path })

export type UploadProgress = (loaded: number, total: number) => void

/**
 * POST *body* raw to *url*, reporting progress; abort through *signal*.
 * XMLHttpRequest because fetch reports no upload progress.
 */
export function uploadRaw<T>(url: string, body: Blob, onProgress: UploadProgress, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    const done = () => signal.removeEventListener('abort', abort)
    xhr.open('POST', url)
    xhr.withCredentials = true
    xhr.setRequestHeader('x-csrf-token', csrfToken)
    xhr.setRequestHeader('content-type', 'application/octet-stream')
    xhr.upload.onprogress = (event) => onProgress(event.loaded, event.lengthComputable ? event.total : body.size)
    xhr.onload = () => {
      done()
      let data: unknown = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        // Fall back to the status text below.
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T)
      else {
        const detail = data && typeof (data as { detail?: unknown }).detail === 'string'
          ? (data as { detail: string }).detail
          : xhr.status === 413 ? 'The file is too large for the server or proxy.' : xhr.statusText || 'Upload failed'
        reject(new ApiError(detail, xhr.status))
      }
    }
    xhr.onerror = () => {
      done()
      reject(new ApiError('Network error. Check the connection.', 0))
    }
    xhr.onabort = () => {
      done()
      reject(new DOMException('Cancelled', 'AbortError'))
    }
    if (signal.aborted) {
      reject(new DOMException('Cancelled', 'AbortError'))
      return
    }
    signal.addEventListener('abort', abort)
    xhr.send(body)
  })
}
/** The working tree against HEAD, or with *commit*, that commit against its parent. */
export const fileDiff = (id: string, path: string, commit?: string) =>
  get<GitDiff>(`${sid(id)}/diff`, commit ? { path, commit } : { path })
export const fileLog = (id: string, path: string) => get<GitLog>(`${sid(id)}/log`, { path })
export const gitBranch = (id: string) => get<GitBranch>(`${sid(id)}/branch`)
export const listChanges = (id: string) => get<GitChanges>(`${sid(id)}/changes`, {}, CHANGES_TIMEOUT)
export const pathCompletions = (id: string, query: string) =>
  get<{ completions: string[] }>(`${sid(id)}/path-completions`, { query }).then((data) => data.completions)

export type OutputRange = { stream: 'log' | 'events'; offset?: number; before?: number; limit?: number }
export const readProcessOutput = (id: string, processId: string, range: OutputRange) =>
  get<ProcessOutput>(
    `${sid(id)}/processes/${encodeURIComponent(processId)}/output`,
    Object.fromEntries(
      Object.entries(range).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)]),
    ),
  )
export const stopProcess = (id: string, processId: string) =>
  post<{ ok: boolean }>(`${sid(id)}/processes/${encodeURIComponent(processId)}/stop`)

export const browseDirectories = (path: string) => get<FsListing>('/api/fs', path ? { path } : {})
/** Create folder *name* inside *path*; resolves to the new folder's path. */
export const makeDirectory = (path: string, name: string) =>
  post<{ path: string }>('/api/fs/mkdir', { path, name }).then((data) => data.path)
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

export const listTerminals = (id: string) => get<TerminalListing>(`${sid(id)}/terminals`)
/** A new shell in the workspace, or in *path*, a folder inside it. */
export const createTerminal = (id: string, size: { cols: number; rows: number }, path?: string) =>
  post<{ terminal: TerminalInfo }>(`${sid(id)}/terminals`, path ? { ...size, path } : size).then((data) => data.terminal)
export const closeTerminal = (id: string, terminalId: string) =>
  post<{ ok: boolean }>(`${sid(id)}/terminals/${encodeURIComponent(terminalId)}/close`)
export const terminalSocketUrl = (id: string, terminalId: string) => {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${location.host}${sid(id)}/terminals/${encodeURIComponent(terminalId)}/ws`
}
