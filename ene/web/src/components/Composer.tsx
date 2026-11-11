import {
  ArrowUp,
  AtSign,
  ChevronDown,
  Clock,
  CornerDownLeft,
  Cpu,
  FileText,
  Folder,
  ListEnd,
  ListPlus,
  SquareSlash,
  Square,
  Undo2,
} from 'lucide-react'
import type { KeyboardEvent } from 'react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { pathCompletions, sessionOptions } from '../lib/api'
import { compactNumber, duration, tokenRate } from '../lib/format'
import type { LiveIndicator } from '../lib/session'
import type { PendingMessage } from '../lib/protocol'
import type { OutboxEntry } from '../lib/useSession'
import { IconButton, Spinner, useNow } from './ui'
import { NO_AUTOFILL } from '../lib/noAutofill'
import { QueryCache, narrowMatches } from '../lib/search'

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
}) {
  const field = useRef<HTMLTextAreaElement>(null)
  const [cursor, setCursor] = useState(draft.length)
  const [index, setIndex] = useState(0)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [paths, setPaths] = useState<{ session: string; query: string; items: string[] }>({ session: '', query: '', items: [] })
  const historyIndex = useRef<number | null>(null)
  const stash = useRef('')
  const composing = useRef(false)

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
    if (!draft.trim() || !connected) return
    if (onSubmit(draft)) {
      historyIndex.current = null
      setText('')
    }
  }

  const recall = (direction: -1 | 1) => {
    const element = field.current
    if (!element || !history.length) return false
    const value = element.value
    const caret = element.selectionStart ?? 0
    if (direction < 0 && value.slice(0, caret).includes('\n')) return false
    if (direction > 0 && value.slice(caret).includes('\n')) return false
    if (historyIndex.current === null) {
      if (direction > 0) return false
      stash.current = draft
      historyIndex.current = history.length - 1
    } else {
      const next = historyIndex.current + direction
      if (next >= history.length) {
        historyIndex.current = null
        setText(stash.current)
        return true
      }
      historyIndex.current = Math.max(0, next)
    }
    setText(history[historyIndex.current])
    return true
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
      if (event.key === 'Escape') {
        event.preventDefault()
        setDismissed(draft)
        return
      }
    }
    if (event.key === 'Escape' && busy) {
      event.preventDefault()
      onCancel()
      return
    }
    if (event.key === 'ArrowUp' && !event.shiftKey && recall(-1)) {
      event.preventDefault()
      return
    }
    if (event.key === 'ArrowDown' && !event.shiftKey && historyIndex.current !== null && recall(1)) {
      event.preventDefault()
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

  const hasText = draft.trim().length > 0
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
      <div className={`composer glass ${connected ? '' : 'offline'}`}>
        {suggestions.length ? (
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
          aria-controls={suggestions.length ? `suggest-${sessionId}` : undefined}
          aria-expanded={suggestions.length > 0}
          onChange={(event) => {
            onDraft(event.target.value)
            setCursor(event.target.selectionStart ?? event.target.value.length)
            historyIndex.current = null
          }}
          onSelect={(event) => setCursor(event.currentTarget.selectionStart ?? 0)}
          onCompositionStart={() => { composing.current = true }}
          onCompositionEnd={() => { composing.current = false }}
          onKeyDown={onKeyDown}
        />
        <div className="composer-bar">
          <IconButton icon={SquareSlash} label="Commands" size="sm" onClick={() => insertToken('/')} />
          <IconButton icon={AtSign} label="Mention a file" size="sm" onClick={() => insertToken('@')} />
          <span className="grow" />
          <ModelPicker
            cwd={cwd}
            model={model}
            detail={modelDetail}
            icon={modelIcon}
            disabled={!connected || busy}
            onModel={onModel}
          />
          {showStop ? (
            <IconButton icon={Square} label="Stop (Esc)" tone="danger" className="send" disabled={!connected} onClick={onCancel} />
          ) : (
            <IconButton
              icon={busy ? ListPlus : ArrowUp}
              label={busy ? 'Queue message' : 'Send (Enter)'}
              tone="accent"
              className="send"
              disabled={!hasText || !connected}
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

/**
 * The session's model, as a compact select. Choosing another configured
 * model sends `/model <alias>`; switching is unavailable mid-round.
 */
function ModelPicker({
  cwd,
  model,
  detail,
  icon,
  disabled,
  onModel,
}: {
  cwd: string
  model: string
  detail?: string
  icon?: string
  disabled: boolean
  onModel: (alias: string) => void
}) {
  const [models, setModels] = useState<string[]>([])
  const load = useCallback(() => {
    sessionOptions(cwd).then((options) => setModels(options.models), () => {})
  }, [cwd])
  useEffect(load, [load])

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
        onFocus={load}
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
