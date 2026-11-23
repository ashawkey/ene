/**
 * Whether file paths in replies resolve, so only openable paths become links.
 * Checks are batched per session and cached; a miss is re-checked after a
 * while, since the agent often names a file before creating it.
 */
import { useEffect, useState } from 'react'

import { existingFiles } from './api'

const MISS_TTL = 5_000
// Keep each request's query string well under common URL limits.
const MAX_BATCH_PATHS = 50
const MAX_BATCH_CHARS = 4_000

const found = new Set<string>()
const checks = new Map<string, Promise<boolean>>()
const queued = new Map<string, Map<string, (ok: boolean) => void>>()

const keyOf = (sessionId: string, path: string) => `${sessionId}\0${path}`

export function fileExists(sessionId: string, path: string): Promise<boolean> {
  const key = keyOf(sessionId, path)
  if (found.has(key)) return Promise.resolve(true)
  let check = checks.get(key)
  if (!check) {
    check = new Promise<boolean>((resolve) => {
      let batch = queued.get(sessionId)
      if (!batch) {
        batch = new Map()
        queued.set(sessionId, batch)
        window.setTimeout(() => void flush(sessionId), 0)
      }
      batch.set(path, resolve)
    })
    checks.set(key, check)
  }
  return check
}

async function flush(sessionId: string) {
  const batch = queued.get(sessionId)
  queued.delete(sessionId)
  if (!batch) return
  const chunks: string[][] = [[]]
  let chars = 0
  for (const path of batch.keys()) {
    const size = encodeURIComponent(path).length + 6
    if (chunks.at(-1)!.length >= MAX_BATCH_PATHS || (chunks.at(-1)!.length && chars + size > MAX_BATCH_CHARS)) {
      chunks.push([])
      chars = 0
    }
    chunks.at(-1)!.push(path)
    chars += size
  }
  await Promise.all(chunks.map(async (paths) => {
    let files: Set<string> | null = null
    try {
      files = new Set(await existingFiles(sessionId, paths))
    } catch {
      // Unknown: render plain text now and ask again on a later render.
    }
    for (const path of paths) {
      const key = keyOf(sessionId, path)
      const ok = files?.has(path) ?? false
      if (ok) found.add(key)
      if (ok || files === null) checks.delete(key)
      else window.setTimeout(() => checks.delete(key), MISS_TTL)
      batch.get(path)!(ok)
    }
  }))
}

/** True once *path* is known to resolve in *sessionId*'s workspace; null skips the check. */
export function useFileExists(sessionId: string | null, path: string | null): boolean {
  const key = sessionId && path ? keyOf(sessionId, path) : null
  const [, setChecked] = useState<string | null>(null)
  useEffect(() => {
    if (!sessionId || !path || found.has(keyOf(sessionId, path))) return
    let live = true
    void fileExists(sessionId, path).then((ok) => {
      if (live && ok) setChecked(keyOf(sessionId, path))
    })
    return () => {
      live = false
    }
  }, [sessionId, path])
  return key !== null && found.has(key)
}
