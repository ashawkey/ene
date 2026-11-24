/**
 * Uploads in flight, shared by every panel. Transfers live outside React so
 * switching tabs or sessions neither cancels nor blocks them; a small queue
 * keeps them from crowding out the page's other requests.
 */
import { useSyncExternalStore } from 'react'

import { attachmentUrl, deleteAttachment, uploadRaw, uploadUrl } from './api'

export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024
export const MAX_BATCH_FILES = 50
export const MAX_BATCH_BYTES = 1024 ** 3
export const MAX_ATTACHMENTS = 10
/** Uploads running at once; the rest wait their turn. */
const PARALLEL = 2
/** Progress is re-rendered at most this often per upload. */
const TICK_MS = 200

export type TransferKind = 'upload' | 'attachment'
export type TransferState = 'queued' | 'running' | 'done' | 'failed'

export type Transfer = {
  id: number
  sessionId: string
  kind: TransferKind
  /** Shown name: the workspace-relative destination or the file name. */
  name: string
  size: number
  loaded: number
  /** Bytes per second, smoothed; 0 until measured. */
  speed: number
  state: TransferState
  error?: string
  /** The saved file's workspace-relative path. */
  path?: string
}

type Job = {
  transfer: Transfer
  url: string
  file: Blob
  controller: AbortController
  done: (ok: boolean) => void
  lastTick: number
  lastLoaded: number
}

let transfers: Transfer[] = []
const jobs = new Map<number, Job>()
let counter = 0
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())

function update(id: number, patch: Partial<Transfer>) {
  transfers = transfers.map((item) => (item.id === id ? { ...item, ...patch } : item))
  const job = jobs.get(id)
  if (job) job.transfer = { ...job.transfer, ...patch }
  emit()
}

function remove(id: number) {
  transfers = transfers.filter((item) => item.id !== id)
  emit()
}

function pump() {
  let running = [...jobs.values()].filter((job) => job.transfer.state === 'running').length
  for (const job of jobs.values()) {
    if (running >= PARALLEL) break
    if (job.transfer.state !== 'queued') continue
    running++
    run(job)
  }
}

function run(job: Job) {
  const { id } = job.transfer
  job.lastTick = performance.now()
  update(id, { state: 'running' })
  uploadRaw<{ path: string }>(
    job.url,
    job.file,
    (loaded) => {
      const now = performance.now()
      const elapsed = now - job.lastTick
      if (elapsed < TICK_MS && loaded < job.transfer.size) return
      const instant = ((loaded - job.lastLoaded) * 1000) / Math.max(elapsed, 1)
      const speed = job.transfer.speed ? job.transfer.speed * 0.7 + instant * 0.3 : instant
      job.lastTick = now
      job.lastLoaded = loaded
      update(id, { loaded, speed })
    },
    job.controller.signal,
  ).then(
    (result) => {
      jobs.delete(id)
      // Finished uploads stay listed until dismissed.
      update(id, { state: 'done', loaded: job.transfer.size, path: result.path })
      job.done(true)
    },
    (exc: unknown) => {
      jobs.delete(id)
      const cancelled = (exc as Error)?.name === 'AbortError'
      if (cancelled) remove(id)
      else update(id, { state: 'failed', error: (exc as Error)?.message || 'Upload failed.' })
      job.done(false)
    },
  ).finally(pump)
}

function enqueue(sessionId: string, kind: TransferKind, name: string, file: Blob, url: string, done: Job['done']): number {
  const id = ++counter
  const transfer: Transfer = { id, sessionId, kind, name, size: file.size, loaded: 0, speed: 0, state: 'queued' }
  transfers = [...transfers, transfer]
  jobs.set(id, { transfer, url, file, controller: new AbortController(), done, lastTick: 0, lastLoaded: 0 })
  emit()
  pump()
  return id
}

/** One file of a batch; *name* is relative to the target folder and may contain `/`. */
export type UploadItem = { file: File; name: string }

/** Why a batch cannot be uploaded, or null. */
export function batchProblem(items: UploadItem[]): string | null {
  if (!items.length) return 'Nothing to upload.'
  if (items.length > MAX_BATCH_FILES) return `Upload at most ${MAX_BATCH_FILES} files at once (got ${items.length}).`
  const big = items.find((item) => item.file.size > MAX_UPLOAD_BYTES)
  if (big) return `${big.name} is over the ${MAX_UPLOAD_BYTES / 1024 / 1024} MB upload limit.`
  if (items.reduce((sum, item) => sum + item.file.size, 0) > MAX_BATCH_BYTES) return 'Upload at most 1 GB at once.'
  return null
}

/**
 * Queue *items* into workspace folder *dir*; *replace* names files that may
 * overwrite existing ones. *onSettled* runs once every item has finished.
 */
export function uploadFiles(
  sessionId: string,
  dir: string,
  items: UploadItem[],
  replace: Set<string>,
  onSettled: (saved: number) => void,
) {
  let left = items.length
  let saved = 0
  const done = (ok: boolean) => {
    if (ok) saved++
    if (--left === 0) onSettled(saved)
  }
  for (const item of items) {
    const shown = dir ? `${dir}/${item.name}` : item.name
    enqueue(sessionId, 'upload', shown, item.file, uploadUrl(sessionId, dir, item.name, replace.has(item.name)), done)
  }
}

/** Queue a message attachment; returns its transfer id. */
export function uploadAttachment(sessionId: string, file: File): number {
  return enqueue(sessionId, 'attachment', file.name || 'pasted file', file, attachmentUrl(sessionId, file.name || 'pasted'), () => {})
}

/** Cancel a queued or running transfer (the hub deletes its partial file). */
export function cancelTransfer(id: number) {
  const job = jobs.get(id)
  if (!job) return
  if (job.transfer.state === 'queued') {
    jobs.delete(id)
    remove(id)
    job.done(false)
  } else job.controller.abort()
}

/** Forget a finished transfer; a saved attachment is deleted from the hub too. */
export function dismissTransfer(id: number) {
  const transfer = transfers.find((item) => item.id === id)
  if (!transfer) return
  if (jobs.has(id)) cancelTransfer(id)
  else remove(id)
  if (transfer.kind === 'attachment' && transfer.path) void deleteAttachment(transfer.sessionId, transfer.path).catch(() => {})
}

/** Forget a sent attachment's transfer, keeping its file. */
export function releaseTransfer(id: number) {
  if (!jobs.has(id)) remove(id)
}

export function useTransfers(): Transfer[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => transfers,
  )
}

export const activeTransfers = () => jobs.size

// Leaving the page aborts every upload, so ask first. A file dropped outside
// a drop zone would otherwise replace the app with the file itself.
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (event) => {
    if (jobs.size) event.preventDefault()
  })
  const refuse = (event: DragEvent) => {
    if (event.defaultPrevented || !hasFiles(event.dataTransfer)) return
    event.preventDefault()
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'
  }
  window.addEventListener('dragover', refuse)
  window.addEventListener('drop', refuse)
}

/** Seconds left at the current speed, or null before it is known. */
export function remaining(transfer: Transfer): number | null {
  if (transfer.state !== 'running' || transfer.speed <= 0) return null
  return Math.max(0, (transfer.size - transfer.loaded) / transfer.speed)
}

/**
 * The files of a drop, with dropped folders walked recursively (names keep
 * the folder path). Entries must be taken synchronously in the drop handler;
 * walking stops after *limit* files.
 */
export async function droppedFiles(data: DataTransfer, limit = MAX_BATCH_FILES + 1): Promise<UploadItem[]> {
  const entries: FileSystemEntry[] = []
  for (const item of [...data.items]) {
    if (item.kind !== 'file') continue
    const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null
    if (entry) entries.push(entry)
  }
  if (!entries.length) return [...data.files].map((file) => ({ file, name: file.name }))
  const out: UploadItem[] = []
  const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (out.length >= limit) return
    if (entry.isFile) {
      const file = await new Promise<File | null>((resolve) => (entry as FileSystemFileEntry).file(resolve, () => resolve(null)))
      if (file) out.push({ file, name: `${prefix}${entry.name}` })
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      // readEntries answers in pages; an empty page ends the folder.
      for (;;) {
        const page = await new Promise<FileSystemEntry[]>((resolve) => reader.readEntries(resolve, () => resolve([])))
        if (!page.length || out.length >= limit) break
        for (const child of page) await walk(child, `${prefix}${entry.name}/`)
      }
    }
  }
  for (const entry of entries) await walk(entry, '')
  return out
}

export const hasFiles = (data: DataTransfer | null) => !!data && [...data.types].includes('Files')

/** Test hook: drop every transfer. */
export function resetTransfers() {
  for (const job of jobs.values()) job.controller.abort()
  jobs.clear()
  transfers = []
  emit()
}
