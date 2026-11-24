import {
  ArrowUp,
  AtSign,
  Brain,
  ChevronDown,
  CircleAlert,
  Clock,
  CornerDownLeft,
  Cpu,
  FileText,
  Folder,
  ListEnd,
  ListPlus,
  Paperclip,
  SquareSlash,
  Square,
  Undo2,
  X,
} from 'lucide-react'
import type { ClipboardEvent, DragEvent, KeyboardEvent } from 'react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { pathCompletions, sessionOptions } from '../lib/api'
import { compactNumber, duration, latency, tokenRate } from '../lib/format'
import type { LiveIndicator } from '../lib/session'
import type { PendingMessage } from '../lib/protocol'
import type { OutboxEntry } from '../lib/useSession'
import { IconButton, LoadingHint, Spinner, useNow } from './ui'
import { NO_AUTOFILL } from '../lib/noAutofill'
import { caretRows, lineBounds } from '../lib/caret'
import { QueryCache, narrowMatches } from '../lib/search'
import { toast } from '../lib/toast'
import {
  MAX_ATTACHMENTS,
  MAX_UPLOAD_BYTES,
  type Transfer,
  dismissTransfer,
  droppedFiles,
  hasFiles,
  releaseTransfer,
  uploadAttachment,
  useTransfers,
} from '../lib/transfers'
import { ProgressBar, transferDetail } from './Transfers'

type Suggestion = { value: string; label: string; detail: string; kind: 'command' | 'path' }

const coarsePointer = () =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches

// Completions per session and query, shared across remounts.
const pathCache = new QueryCache<string[]>()

export function Composer({
  sessionId,
  draft,
  onDraft,
  onSubmit,
  onCancel,
  onWithdraw,
  busy,
  connected,
  commands,
  history,
  pending,
  outbox,
  withdrawing,
  indicator,
  operationSince,
  runningProcesses,
  onProcesses,
  focusSignal,
  cwd,
  model,
  modelDetail,
  modelIcon,
  onModel,
  effort,
  onEffort,
}: {
  sessionId: string
  draft: string
  onDraft: (text: string) => void
  onSubmit: (text: string) => boolean
  onCancel: () => void
  onWithdraw: () => void
  busy: boolean
  connected: boolean
  commands: Record<string, string>
  history: string[]
  pending: PendingMessage | null
  outbox: OutboxEntry[]
  withdrawing: boolean
  indicator: LiveIndicator | null
  operationSince: number | null
  /** Background processes still running; shown until they all exit. */
  runningProcesses: number
  onProcesses: () => void
  focusSignal: number
  /** Session workspace, for listing configured models. */
  cwd: string
  /** Current model alias. */
  model: string
  /** Resolved model name, shown after the alias when it differs. */
  modelDetail?: string
  modelIcon?: string
  /** Switch models; same as `/model <alias>`. */
  onModel: (alias: string) => void
  /** Current reasoning effort; absent when the model has no effort control. */
  effort?: string
  /** Set reasoning effort; same as `/effort <level>`. */
  onEffort: (level: string) => void
}) {
  const field = useRef<HTMLTextAreaElement>(null)
  const [cursor, setCursor] = useState(draft.length)
  const [index, setIndex] = useState(0)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [paths, setPaths] = useState<{ session: string; query: string; items: string[] }>({ session: '', query: '', items: [] })
  const historyIndex = useRef<number | null>(null)
  const stash = useRef('')
  const composing = useRef(false)
  const [options, reloadOptions] = useSessionOptions(cwd)
  // Message attachments: transfer ids, in the order they were added.
  const [attachments, setAttachments] = useState<number[]>([])
  const [dragging, setDragging] = useState(false)
  const filePicker = useRef<HTMLInputElement>(null)
  const transfers = useTransfers()
  const attached = attachments
    .map((id) => transfers.find((item) => item.id === id))
    .filter((item): item is Transfer => item !== undefined)
  const attaching = attached.some((item) => item.state === 'queued' || item.state === 'running')

  useEffect(() => {
    if (focusSignal) field.current?.focus()
  }, [focusSignal])

  // Grow with content up to the CSS max-height; only scroll beyond it.
  // scrollHeight is rounded to whole pixels while line boxes can be
  // fractional (15.5px × 1.5), so pad by a pixel to avoid a spurious overflow.
  const fitHeight = useCallback(() => {
    const element = field.current
    if (!element) return
    element.style.overflowY = 'hidden'
    element.style.height = 'auto'
    const needed = element.scrollHeight + 1
    const max = parseFloat(getComputedStyle(element).maxHeight)
    const limited = Number.isFinite(max) && needed > max
    element.style.height = `${limited ? max : needed}px`
    element.style.overflowY = limited ? 'auto' : 'hidden'
  }, [])

  useLayoutEffect(fitHeight, [draft, fitHeight])

  // Re-measure when the composer width changes and text re-wraps.
  useEffect(() => {
    const element = field.current
    if (!element || typeof ResizeObserver === 'undefined') return
    let width = element.clientWidth
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === width) return
      width = element.clientWidth
      fitHeight()
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [fitHeight])

  const commandQuery = /^\/[\w:-]*$/.test(draft) ? draft.slice(1).toLowerCase() : null
  const before = draft.slice(0, cursor)
  const pathMatch = before.match(/(?:^|\s)@([^\s@]*)$/)
  const pathQuery = pathMatch ? pathMatch[1] : null

  useEffect(() => {
    if (pathQuery === null || dismissed === draft) return
    const key = `${sessionId}\u0000${pathQuery}`
    const cached = pathCache.get(key)
    if (cached) {
      setPaths({ session: sessionId, query: pathQuery, items: cached })
      return
    }
    let cancelled = false
    const timer = window.setTimeout(() => {
      pathCompletions(sessionId, pathQuery)
        .then((items) => {
          pathCache.set(key, items)
          if (!cancelled) setPaths({ session: sessionId, query: pathQuery, items })
        })
        .catch(() => !cancelled && setPaths({ session: sessionId, query: pathQuery, items: [] }))
    }, 50)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [pathQuery, sessionId, dismissed, draft])

  const suggestions = useMemo<Suggestion[]>(() => {
    if (dismissed === draft) return []
    if (commandQuery !== null) {
      const list = Object.entries(commands)
        .filter(([name]) => name.toLowerCase().includes(commandQuery))
        .sort(([a], [b]) => Number(!a.toLowerCase().startsWith(commandQuery)) - Number(!b.toLowerCase().startsWith(commandQuery)) || a.localeCompare(b))
        .map(([name, detail]) => ({ value: `/${name}`, label: `/${name}`, detail, kind: 'command' as const }))
      return list.length === 1 && list[0].value === draft ? [] : list.slice(0, 50)
    }
    // Until this query's answer arrives, narrow the previous one locally.
    const items = pathQuery !== null && paths.session === sessionId
      ? narrowMatches(paths.items, (item) => item.replace(/^@/, ''), paths.query, pathQuery)
      : null
    if (pathQuery !== null && items) {
      const list = items.map((item) => {
        const value = item.startsWith('@') ? item : `@${item}`
        return { value, label: value.slice(1), detail: value.endsWith('/') ? 'folder' : 'file', kind: 'path' as const }
      })
      return list.length === 1 && list[0].value === `@${pathQuery}` ? [] : list.slice(0, 50)
    }
    return []
  }, [commandQuery, commands, dismissed, draft, pathQuery, paths, sessionId])

  useEffect(() => setIndex(0), [suggestions.length, commandQuery, pathQuery])

  // While an @ query is in flight, or after it found nothing, keep the popup
  // open with a note instead of showing it only once results arrive.
  const pathOpen = pathQuery !== null && commandQuery === null && dismissed !== draft
  const pathAnswered = paths.session === sessionId && paths.query === pathQuery
  const pathNote = !pathOpen ? null : !pathAnswered ? 'searching' : paths.items.length ? null : 'none'
  const popup = suggestions.length > 0 || pathNote !== null

  const setText = useCallback((text: string, caret = text.length) => {
    onDraft(text)
    setCursor(caret)
    requestAnimationFrame(() => {
      field.current?.setSelectionRange(caret, caret)
    })
  }, [onDraft])

  const apply = (suggestion: Suggestion) => {
    if (suggestion.kind === 'command') {
      setText(`${suggestion.value} `)
      return
    }
    const start = cursor - (pathQuery?.length ?? 0) - 1
    const tail = draft.slice(cursor)
    const insert = suggestion.value.endsWith('/') ? suggestion.value : `${suggestion.value} `
    setText(draft.slice(0, start) + insert + tail, start + insert.length)
  }

  const send = () => {
    if (!connected) return
    if (attaching) {
      toast('Wait for the attachments to finish uploading.', 'info')
      return
    }
    const saved = attached.filter((item) => item.state === 'done' && item.path)
    if (!draft.trim() && !saved.length) return
    const refs = saved.map((item) => `@${item.path}`).join(' ')
    const text = saved.length ? `${draft.trimEnd()}${draft.trim() ? '\n\n' : ''}Attached: ${refs}` : draft
    if (onSubmit(text)) {
      historyIndex.current = null
      setText('')
      // Sent: the files stay for the agent; only failed chips are dropped.
      attached.forEach((item) => (item.state === 'done' ? releaseTransfer(item.id) : dismissTransfer(item.id)))
      setAttachments([])
    }
  }

  const attach = (files: File[]) => {
    if (!files.length) return
    const room = MAX_ATTACHMENTS - attachments.length
    if (files.length > room) toast(`Attach at most ${MAX_ATTACHMENTS} files to a message.`)
    const accepted: number[] = []
    for (const file of files.slice(0, Math.max(0, room))) {
      if (file.size > MAX_UPLOAD_BYTES) {
        toast(`${file.name} is over the ${MAX_UPLOAD_BYTES / 1024 / 1024} MB upload limit.`)
        continue
      }
      accepted.push(uploadAttachment(sessionId, file))
    }
    if (accepted.length) setAttachments((current) => [...current, ...accepted])
    field.current?.focus()
  }
  const detachFile = (id: number) => {
    dismissTransfer(id)
    setAttachments((current) => current.filter((item) => item !== id))
  }
  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...event.clipboardData.files]
    if (!files.length) return
    event.preventDefault()
    attach(files)
  }
  const dropProps = {
    onDragOver: (event: DragEvent) => {
      if (!hasFiles(event.dataTransfer)) return
      event.preventDefault()
      event.dataTransfer.dropEffect = 'copy'
      setDragging(true)
    },
    onDragLeave: (event: DragEvent) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
    },
    onDrop: (event: DragEvent) => {
      if (!hasFiles(event.dataTransfer)) return
      event.preventDefault()
      setDragging(false)
      // Folders are flattened; attachments are single files.
      void droppedFiles(event.dataTransfer, MAX_ATTACHMENTS + 1).then((items) => attach(items.map((item) => item.file)))
    },
  }

  // Up/Down move through the text first; only on its first (or last) visual
  // row do they step through sent messages. A recalled entry opens with the
  // caret on its first line going up and at its end going down, so repeated
  // presses keep walking the history.
  const recall = (direction: -1 | 1) => {
    const element = field.current
    if (!element || !history.length) return false
    if (element.selectionStart !== element.selectionEnd) return false
    const rows = caretRows(element)
    if (direction < 0 ? !rows.first : !rows.last) return false
    let text: string
    if (historyIndex.current === null) {
      if (direction > 0) return false
      stash.current = draft
      historyIndex.current = history.length - 1
      text = history[historyIndex.current]
    } else {
      const next = historyIndex.current + direction
      if (next < 0) return false
      if (next >= history.length) {
        historyIndex.current = null
        text = stash.current
      } else {
        historyIndex.current = next
        text = history[next]
      }
    }
    setText(text, direction < 0 ? lineBounds(text, 0)[1] : text.length)
    return true
  }

  // Home/End go to the start/end of the current line, then of the whole text.
  const jumpLine = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const element = event.currentTarget
    const { value, selectionStart: start, selectionEnd: end, selectionDirection } = element
    const backward = selectionDirection === 'backward'
    // Shift extends from the selection's anchor; otherwise collapse first.
    const anchor = backward ? end : start
    const focus = event.shiftKey ? (backward ? start : end) : (event.key === 'Home' ? start : end)
    const [lineStart, lineEnd] = lineBounds(value, focus)
    const target = event.key === 'Home'
      ? (focus === lineStart ? 0 : lineStart)
      : (focus === lineEnd ? value.length : lineEnd)
    event.preventDefault()
    if (event.shiftKey) {
      element.setSelectionRange(Math.min(anchor, target), Math.max(anchor, target), target < anchor ? 'backward' : 'forward')
    } else {
      element.setSelectionRange(target, target)
    }
    setCursor(element.selectionStart)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition (Chinese/Japanese input) owns Enter and arrows.
    if (event.nativeEvent.isComposing || composing.current || event.keyCode === 229) return
    if (suggestions.length) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const step = event.key === 'ArrowDown' ? 1 : -1
        setIndex((current) => (current + step + suggestions.length) % suggestions.length)
        return
      }
      if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
        event.preventDefault()
        apply(suggestions[Math.min(index, suggestions.length - 1)])
        return
      }
    }
    if (popup && event.key === 'Escape') {
      event.preventDefault()
      setDismissed(draft)
      return
    }
    if (event.key === 'Escape' && busy) {
      event.preventDefault()
      onCancel()
      return
    }
    const plain = !event.altKey && !event.ctrlKey && !event.metaKey
    if (plain && !event.shiftKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
      if (recall(event.key === 'ArrowUp' ? -1 : 1)) event.preventDefault()
      return
    }
    if (plain && (event.key === 'Home' || event.key === 'End')) {
      jumpLine(event)
      return
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !coarsePointer()) {
      event.preventDefault()
      send()
    }
  }

  const insertToken = (token: string) => {
    const element = field.current
    const caret = element?.selectionStart ?? draft.length
    const head = draft.slice(0, caret)
    const spacer = head && !/\s$/.test(head) ? ' ' : ''
    if (token === '/' && draft.trim() === '') {
      setText('/')
    } else {
      const next = `${head}${spacer}${token}${draft.slice(caret)}`
      setText(next, caret + spacer.length + token.length)
    }
    setDismissed(null)
    field.current?.focus()
  }

  const hasText = draft.trim().length > 0 || attached.some((item) => item.state === 'done')
  const showStop = busy && !hasText

  return (
    <div className="composer-dock">
      <StatusStrip
        indicator={indicator}
        busy={busy}
        since={operationSince}
        processes={runningProcesses}
        onProcesses={onProcesses}
      />
      {outbox.length || pending ? (
        <div className="queue">
          {outbox.map((entry) => (
            <div className="queue-chip sending" key={entry.actionId} data-tip={entry.attempts > 1 ? 'Re-sending after reconnect' : 'Sending'}>
              <Spinner size={12} />
              <span className="clamp">{entry.text}</span>
            </div>
          ))}
          {pending ? (
            <PendingChip
              pending={pending}
              withdrawing={withdrawing}
              canWithdraw={connected && !withdrawing && !hasText}
              onWithdraw={onWithdraw}
            />
          ) : null}
        </div>
      ) : null}
      <div className={`composer glass ${connected ? '' : 'offline'} ${dragging ? 'drop-target' : ''}`} {...dropProps}>
        {popup ? (
          <div className="suggestions" role="listbox" id={`suggest-${sessionId}`}>
            {suggestions.map((item, position) => (
              <button
                type="button"
                role="option"
                aria-selected={position === index}
                className={position === index ? 'selected' : ''}
                key={item.value}
                onMouseDown={(event) => {
                  event.preventDefault()
                  apply(item)
                }}
                onMouseEnter={() => setIndex(position)}
              >
                {item.kind === 'command' ? <SquareSlash size={14} /> : item.detail === 'folder' ? <Folder size={14} /> : <FileText size={14} />}
                <span className="s-label">{item.label}</span>
                {item.kind === 'command' ? <span className="s-detail">{item.detail}</span> : null}
                {position === index ? <CornerDownLeft size={12} className="s-enter" /> : null}
              </button>
            ))}
            {pathNote === 'searching' ? <LoadingHint>Searching files…</LoadingHint> : null}
            {pathNote === 'none' ? <div className="s-none">No matching files.</div> : null}
          </div>
        ) : null}
        {attached.length ? (
          <div className="attachments" aria-label="Attachments">
            {attached.map((item) => (
              <div className={`attachment ${item.state}`} key={item.id} data-tip={item.state === 'done' ? item.path : transferDetail(item)}>
                {item.state === 'failed' ? <CircleAlert size={13} className="fail" /> : <Paperclip size={13} />}
                <span className="clamp">{item.name}</span>
                {item.state === 'queued' || item.state === 'running'
                  ? <ProgressBar value={item.size ? Math.round((item.loaded / item.size) * 100) : 0} state={item.state} />
                  : null}
                <button type="button" aria-label={`Remove ${item.name}`} onClick={() => detachFile(item.id)}><X size={12} /></button>
              </div>
            ))}
          </div>
        ) : null}
        <textarea
          {...NO_AUTOFILL}
          ref={field}
          rows={1}
          value={draft}
          placeholder={
            !connected
              ? 'Reconnecting… your draft is kept'
              : (busy ? 'Queue a follow-up…' : 'Message ene  ·  / commands  ·  @ files  ·  ! shell')
          }
          aria-label="Message"
          aria-controls={popup ? `suggest-${sessionId}` : undefined}
          aria-expanded={popup}
          onChange={(event) => {
            onDraft(event.target.value)
            setCursor(event.target.selectionStart ?? event.target.value.length)
            historyIndex.current = null
          }}
          onSelect={(event) => setCursor(event.currentTarget.selectionStart ?? 0)}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={() => { composing.current = false }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />
        <input
          ref={filePicker}
          type="file"
          multiple
          hidden
          aria-hidden
          tabIndex={-1}
          onChange={(event) => {
            const files = [...(event.target.files ?? [])]
            event.target.value = ''
            attach(files)
          }}
        />
        <div className="composer-bar">
          <IconButton icon={SquareSlash} label="Commands" size="sm" onClick={() => insertToken('/')} />
          <IconButton icon={AtSign} label="Mention a file" size="sm" onClick={() => insertToken('@')} />
          <IconButton icon={Paperclip} label="Attach files (or drop or paste them)" size="sm" disabled={!connected} onClick={() => filePicker.current?.click()} />
          <span className="grow" />
          <ModelPicker
            models={options.models}
            reload={reloadOptions}
            model={model}
            detail={modelDetail}
            icon={modelIcon}
            disabled={!connected || busy}
            onModel={onModel}
          />
          {effort ? (
            <EffortPicker
              efforts={options.reasoning_efforts}
              effort={effort}
              disabled={!connected}
              onEffort={onEffort}
            />
          ) : null}
          {showStop ? (
            <IconButton icon={Square} label="Stop (Esc)" tone="danger" className="send" disabled={!connected} onClick={onCancel} />
          ) : (
            <IconButton
              icon={busy ? ListPlus : ArrowUp}
              label={attaching ? 'Uploading attachments…' : busy ? 'Queue message' : 'Send (Enter)'}
              tone="accent"
              className="send"
              busy={attaching}
              disabled={!hasText || !connected || attaching}
              onClick={send}
            />
          )}
        </div>
      </div>
    </div>
  )
}

function StatusStrip({
  indicator,
  busy,
  since,
  processes,
  onProcesses,
}: {
  indicator: LiveIndicator | null
  busy: boolean
  since: number | null
  processes: number
  onProcesses: () => void
}) {
  const now = useNow(busy)
  if (!busy && !processes) return null
  const started = indicator?.started_at ?? since
  const elapsed = started ? now / 1000 - started : 0
  const countdown = indicator?.countdown
  const label = indicator?.label || 'Working'
  return (
    <div className="status-strip">
      {busy ? (
        <span className="status-live">
          <span className="pulse" />
          <span className="status-label">{label}</span>
          <span className="muted">
            {countdown ? duration(Math.max(0, countdown - (now - indicator!.receivedAt) / 1000)) : duration(elapsed)}
          </span>
          {indicator && indicator.input_tokens ? (
            <span className="muted">↑{compactNumber(indicator.input_tokens)} ↓{compactNumber(indicator.output_tokens ?? 0)}</span>
          ) : indicator?.suffix ? <span className="muted clamp">{indicator.suffix}</span> : null}
          {typeof indicator?.ttft === 'number' ? (
            <span className="muted" data-tip="Time to first token of the streaming response">TTFT {latency(indicator.ttft)}</span>
          ) : null}
          {indicator?.tps ? (
            <span className="muted" data-tip="Estimated output speed of the streaming response">~{tokenRate(indicator.tps)}</span>
          ) : null}
          <span className="muted kbd">esc</span>
        </span>
      ) : null}
      {processes ? (
        <button type="button" className="status-procs" onClick={onProcesses} data-tip="Background processes">
          <span className="proc-dot" />{processes} running
        </button>
      ) : null}
    </div>
  )
}

/**
 * The queued message. A plain message (clock) joins the running round at its
 * next step; a `/wait` message (list-end) waits for the round to finish, plus
 * any remaining delay, shown in parentheses.
 */
function PendingChip({
  pending,
  withdrawing,
  canWithdraw,
  onWithdraw,
}: {
  pending: PendingMessage
  withdrawing: boolean
  canWithdraw: boolean
  onWithdraw: () => void
}) {
  const waits = pending.steer === false
  const readyAt = pending.ready_at ?? null
  const now = useNow(readyAt !== null)
  const remaining = readyAt === null ? 0 : readyAt - now / 1000
  const delay = remaining > 0 ? duration(Math.ceil(remaining)) : null
  const tip = waits
    ? delay
      ? `Waiting — runs after the current round, in ${delay} at the earliest`
      : 'Waiting — runs after the current round'
    : 'Queued — joins the current round at its next step'
  return (
    <div className={`queue-chip ${waits ? 'waiting' : 'steering'}`}>
      {waits ? <ListEnd size={13} aria-label="Waits for the round" /> : <Clock size={13} aria-label="Joins the round" />}
      <span className="clamp" data-tip={tip}>{pending.text}</span>
      {delay ? <span className="queue-delay">({delay})</span> : null}
      <IconButton
        icon={Undo2}
        size="sm"
        label="Edit queued message"
        busy={withdrawing}
        disabled={!canWithdraw}
        onClick={onWithdraw}
      />
    </div>
  )
}

/** Configured models and reasoning efforts; `reload` refetches after config edits. */
function useSessionOptions(cwd: string): [{ models: string[]; reasoning_efforts: string[] }, () => void] {
  const [options, setOptions] = useState<{ models: string[]; reasoning_efforts: string[] }>({ models: [], reasoning_efforts: [] })
  const reload = useCallback(() => {
    sessionOptions(cwd).then(
      (data) => setOptions({ models: data.models, reasoning_efforts: data.reasoning_efforts }),
      () => {},
    )
  }, [cwd])
  useEffect(reload, [reload])
  return [options, reload]
}

/**
 * The session's model, as a compact select. Choosing another configured
 * model sends `/model <alias>`; switching is unavailable mid-round.
 */
function ModelPicker({
  models,
  reload,
  model,
  detail,
  icon,
  disabled,
  onModel,
}: {
  models: string[]
  reload: () => void
  model: string
  detail?: string
  icon?: string
  disabled: boolean
  onModel: (alias: string) => void
}) {
  const choices = model && !models.includes(model) ? [model, ...models] : models
  if (!choices.length) return null
  const label = detail && model && detail !== model ? `${model} (${detail})` : model || detail || 'model'
  const tip = disabled ? 'Switch models between rounds' : 'Switch model (/model)'
  return (
    <label className={`model-picker ${disabled ? 'disabled' : ''}`} data-tip={tip}>
      {icon ? <span className="model-icon">{icon}</span> : <Cpu size={13} />}
      <span className="clamp">{label}</span>
      <ChevronDown size={12} className="model-caret" />
      <select
        value={model}
        aria-label="Model"
        disabled={disabled}
        onFocus={reload}
        onChange={(event) => {
          const next = event.target.value
          if (next && next !== model) onModel(next)
        }}
      >
        {choices.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
    </label>
  )
}

/**
 * The session's reasoning effort, as a compact select. Choosing a level sends
 * `/effort <level>`, which applies from the next model request, so it stays
 * available mid-round.
 */
function EffortPicker({
  efforts,
  effort,
  disabled,
  onEffort,
}: {
  efforts: string[]
  effort: string
  disabled: boolean
  onEffort: (level: string) => void
}) {
  const choices = efforts.includes(effort) ? efforts : [effort, ...efforts]
  const tip = 'Reasoning effort (/effort)'
  return (
    <label className={`model-picker ${disabled ? 'disabled' : ''}`} data-tip={tip}>
      <Brain size={13} />
      <span className="clamp">{effort}</span>
      <ChevronDown size={12} className="model-caret" />
      <select
        value={effort}
        aria-label="Reasoning effort"
        disabled={disabled}
        onChange={(event) => {
          const next = event.target.value
          if (next && next !== effort) onEffort(next)
        }}
      >
        {choices.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
    </label>
  )
}
