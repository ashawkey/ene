import { AtSign, CircleAlert, CircleCheck, Upload, X } from 'lucide-react'
import { useCallback, useState } from 'react'

import { errorMessage, existingUploads } from '../lib/api'
import { bytes, duration } from '../lib/format'
import {
  type Transfer,
  type UploadItem,
  batchProblem,
  cancelTransfer,
  dismissTransfer,
  remaining,
  uploadFiles,
  useTransfers,
} from '../lib/transfers'
import { toast } from '../lib/toast'
import { IconButton, Modal } from './ui'

/** Progress bar width for *transfer*, 0–100. */
const percentOf = (transfer: Transfer) =>
  transfer.size ? Math.min(100, Math.round((transfer.loaded / transfer.size) * 100)) : transfer.state === 'done' ? 100 : 0

/** Size, speed, and time left of a running transfer, or its outcome. */
export function transferDetail(transfer: Transfer): string {
  if (transfer.state === 'failed') return transfer.error ?? 'Failed'
  if (transfer.state === 'queued') return `Waiting · ${bytes(transfer.size)}`
  if (transfer.state === 'done') return bytes(transfer.size)
  const left = remaining(transfer)
  const speed = transfer.speed ? ` · ${bytes(Math.round(transfer.speed))}/s` : ''
  return `${bytes(transfer.loaded)} of ${bytes(transfer.size)}${speed}${left !== null ? ` · ${duration(Math.ceil(left))} left` : ''}`
}

export function ProgressBar({ value, state }: { value: number; state: Transfer['state'] }) {
  return (
    <span className={`progress ${state}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
      <span style={{ width: `${value}%` }} />
    </span>
  )
}

/** Workspace uploads of one session: progress and cancel while running; finished
 * rows stay until dismissed and can be mentioned in the message. */
export function TransferList({ sessionId, onMention }: { sessionId: string; onMention: (path: string) => void }) {
  const items = useTransfers().filter((item) => item.sessionId === sessionId && item.kind === 'upload')
  if (!items.length) return null
  const active = items.filter((item) => item.state === 'queued' || item.state === 'running')
  const finished = items.filter((item) => item.state === 'done' || item.state === 'failed')
  const total = active.reduce((sum, item) => sum + item.size, 0)
  const loaded = active.reduce((sum, item) => sum + item.loaded, 0)
  const saved = items.filter((item) => item.state === 'done').length
  return (
    <div className="transfers" role="region" aria-label="Uploads">
      <div className="transfers-head">
        <Upload size={13} />
        <span className="grow">
          {active.length
            ? `Uploading ${active.length} file${active.length === 1 ? '' : 's'} · ${bytes(loaded)} of ${bytes(total)}`
            : `Uploaded ${saved} of ${items.length} file${items.length === 1 ? '' : 's'}`}
        </span>
        {active.length > 1 ? (
          <button type="button" className="link-btn" onClick={() => active.forEach((item) => cancelTransfer(item.id))}>Cancel all</button>
        ) : null}
        {!active.length ? (
          <IconButton icon={X} size="sm" label="Close uploads" onClick={() => finished.forEach((item) => dismissTransfer(item.id))} />
        ) : null}
      </div>
      <div className="transfers-list">
        {items.map((item) => {
          const running = item.state === 'queued' || item.state === 'running'
          return (
            <div className={`transfer ${item.state}`} key={item.id}>
              {item.state === 'done' ? <CircleCheck size={14} className="ok" /> : item.state === 'failed' ? <CircleAlert size={14} className="fail" /> : null}
              <div className="transfer-main">
                <span className="clamp mono" title={item.name}>{item.name}</span>
                {running ? <ProgressBar value={percentOf(item)} state={item.state} /> : null}
                <span className="transfer-detail">{transferDetail(item)}</span>
              </div>
              {item.state === 'done' && item.path ? (
                <IconButton icon={AtSign} size="sm" label={`Mention ${item.path} in message`} onClick={() => onMention(item.path!)} />
              ) : null}
              <IconButton
                icon={X}
                size="sm"
                label={running ? `Cancel upload of ${item.name}` : `Dismiss ${item.name}`}
                onClick={() => (running ? cancelTransfer(item.id) : dismissTransfer(item.id))}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}

type Conflict = { dir: string; items: UploadItem[]; existing: string[] }

/**
 * Upload files into a workspace folder: check the limits, ask what to do
 * about files that already exist, then queue them. Render `dialog`.
 */
export function useWorkspaceUpload(sessionId: string, onSettled: (saved: number) => void) {
  const [conflict, setConflict] = useState<Conflict | null>(null)

  const start = useCallback((dir: string, items: UploadItem[], replace: Set<string>) => {
    if (!items.length) return
    uploadFiles(sessionId, dir, items, replace, onSettled)
  }, [sessionId, onSettled])

  const upload = useCallback(async (dir: string, items: UploadItem[]) => {
    const problem = batchProblem(items)
    if (problem) {
      toast(problem)
      return
    }
    let existing: string[]
    try {
      existing = await existingUploads(sessionId, dir, items.map((item) => item.name))
    } catch (exc) {
      toast(errorMessage(exc, 'Could not upload to this folder.'))
      return
    }
    if (existing.length) setConflict({ dir, items, existing })
    else start(dir, items, new Set())
  }, [sessionId, start])

  const resolve = (choice: 'replace' | 'skip' | 'cancel') => {
    const current = conflict
    setConflict(null)
    if (!current || choice === 'cancel') return
    const existing = new Set(current.existing)
    if (choice === 'replace') start(current.dir, current.items, existing)
    else start(current.dir, current.items.filter((item) => !existing.has(item.name)), new Set())
  }

  const dialog = conflict ? (
    <Modal onClose={() => resolve('cancel')} label="Replace existing files?">
      <div className="modal-head"><Upload size={18} /><h2>Replace existing files?</h2></div>
      <div className="conflict-body">
        <p>
          {conflict.existing.length === 1
            ? <><span className="mono">{conflict.existing[0]}</span> already exists{conflict.dir ? <> in <span className="mono">{conflict.dir}</span></> : null}.</>
            : <>{conflict.existing.length} of {conflict.items.length} files already exist{conflict.dir ? <> in <span className="mono">{conflict.dir}</span></> : null}.</>}
        </p>
        {conflict.existing.length > 1 ? (
          <ul className="conflict-list">
            {conflict.existing.slice(0, 8).map((name) => <li key={name} className="mono clamp">{name}</li>)}
            {conflict.existing.length > 8 ? <li className="muted">and {conflict.existing.length - 8} more</li> : null}
          </ul>
        ) : null}
      </div>
      <div className="modal-foot conflict-foot">
        <button type="button" className="btn" onClick={() => resolve('cancel')}>Cancel</button>
        {conflict.existing.length < conflict.items.length ? (
          <button type="button" className="btn" onClick={() => resolve('skip')}>Skip existing</button>
        ) : null}
        <button type="button" className="btn primary" onClick={() => resolve('replace')}>Replace</button>
      </div>
    </Modal>
  ) : null

  return { upload, dialog }
}
