import {
  ArrowUp,
  AtSign,
  Clock,
  CornerDownLeft,
  FileText,
  Folder,
  ListPlus,
  SquareSlash,
  Square,
  Undo2,
} from 'lucide-react'
import type { KeyboardEvent } from 'react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { pathCompletions } from '../lib/api'
import { compactNumber, duration } from '../lib/format'
import type { LiveIndicator, LiveProcesses } from '../lib/session'
import type { PendingMessage } from '../lib/protocol'
import type { OutboxEntry } from '../lib/useSession'
import { IconButton, Spinner, useNow } from './ui'

type Suggestion = { value: string; label: string; detail: string; kind: 'command' | 'path' }

const coarsePointer = () =>
  typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches

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
  processes,
  onOpenTasks,
  focusSignal,
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
  processes: LiveProcesses
  onOpenTasks: () => void
  focusSignal: number
}) {
  const field = useRef<HTMLTextAreaElement>(null)
  const [cursor, setCursor] = useState(draft.length)
  const [index, setIndex] = useState(0)
  const [dismissed, setDismissed] = useState<string | null>(null)
  const [paths, setPaths] = useState<{ query: string; items: string[] }>({ query: '', items: [] })
  const historyIndex = useRef<number | null>(null)
  const stash = useRef('')
  const composing = useRef(false)

  useEffect(() => {
    if (focusSignal) field.current?.focus()
  }, [focusSignal])

  // Grow with content up to the CSS max-height, then scroll.
  useLayoutEffect(() => {
    const element = field.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [draft])

  const commandQuery = /^\/[\w:-]*$/.test(draft) ? draft.slice(1).toLowerCase() : null
  const before = draft.slice(0, cursor)
  const pathMatch = before.match(/(?:^|\s)@([^\s@]*)$/)
  const pathQuery = pathMatch ? pathMatch[1] : null

  useEffect(() => {
    if (pathQuery === null || dismissed === draft) return
    let cancelled = false
    const timer = window.setTimeout(() => {
      pathCompletions(sessionId, pathQuery)
        .then((items) => !cancelled && setPaths({ query: pathQuery, items }))
        .catch(() => !cancelled && setPaths({ query: pathQuery, items: [] }))
    }, 120)
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
    if (pathQuery !== null && paths.query === pathQuery) {
      const list = paths.items.map((item) => {
        const value = item.startsWith('@') ? item : `@${item}`
        return { value, label: value.slice(1), detail: value.endsWith('/') ? 'folder' : 'file', kind: 'path' as const }
      })
      return list.length === 1 && list[0].value === `@${pathQuery}` ? [] : list.slice(0, 50)
    }
    return []
  }, [commandQuery, commands, dismissed, draft, pathQuery, paths])

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
        processes={processes}
        onOpenTasks={onOpenTasks}
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
            <div className="queue-chip">
              <Clock size={13} />
              <span className="clamp" data-tip="Queued — runs after the current round">{pending.text}</span>
              <IconButton
                icon={Undo2}
                size="sm"
                label="Edit queued message"
                busy={withdrawing}
                disabled={!connected || withdrawing || hasText}
                onClick={onWithdraw}
              />
            </div>
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
          ref={field}
          rows={1}
          value={draft}
          placeholder={connected ? (busy ? 'Queue a follow-up…' : 'Message ene  ·  / commands  ·  @ files') : 'Reconnecting… your draft is kept'}
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
  onOpenTasks,
}: {
  indicator: LiveIndicator | null
  busy: boolean
  since: number | null
  processes: LiveProcesses
  onOpenTasks: () => void
}) {
  const now = useNow(busy || processes.running > 0)
  const running = processes.running
  if (!busy && !running) return null
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
          <span className="muted kbd">esc</span>
        </span>
      ) : null}
      {running ? (
        <button type="button" className="status-procs" onClick={onOpenTasks} data-tip="Background processes">
          <span className="proc-dot" />
          {running} running
        </button>
      ) : null}
    </div>
  )
}
