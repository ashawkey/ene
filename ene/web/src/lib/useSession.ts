import { useCallback, useEffect, useRef, useState } from 'react'

import { setCsrf } from './api'
import { newActionId } from './ids'
import type { ClientAction, ServerEvent } from './protocol'
import type { SessionView, Signal } from './session'
import { initialView, reduce } from './session'
import { useSocket } from './socket'
import { store } from './storage'
import { toast } from './toast'

export type OutboxEntry = { actionId: string; text: string; sentAt: number; attempts: number }

const draftKey = (id: string) => `ene.draft.${id}`
// Guards against a double Enter / double tap re-sending the same text.
const DOUBLE_SEND_WINDOW = 1500
// High-frequency events whose renders are coalesced per animation frame.
const BATCHED = new Set<string>(['assistant_delta', 'thinking_delta', 'thinking_update', 'output', 'debug'])

/**
 * One attached session: its event stream, derived view, composer draft, and
 * an outbox that makes submissions exactly-once from the user's perspective.
 *
 * A submit stays in the outbox until the hub acknowledges it. After a
 * reconnect every unacknowledged submit is re-sent with its original
 * action_id; the hub remembers action ids, so a message that did reach the
 * agent before the drop is not delivered twice. A rejection puts the text back
 * into the composer instead of losing it.
 */
export function useSession(sessionId: string, onGone: () => void) {
  const [view, setView] = useState<SessionView>(initialView)
  const viewRef = useRef(view)
  const [draft, setDraftState] = useState(() => store.get(draftKey(sessionId)) ?? '')
  const draftRef = useRef(draft)
  const [outbox, setOutbox] = useState<OutboxEntry[]>([])
  const outboxRef = useRef<OutboxEntry[]>([])
  const withdrawRef = useRef<string | null>(null)
  const [withdrawing, setWithdrawing] = useState(false)

  const setDraft = useCallback((text: string) => {
    draftRef.current = text
    setDraftState(text)
    store.set(draftKey(sessionId), text)
  }, [sessionId])

  const restoreDraft = useCallback((text: string) => {
    if (!text) return
    const current = draftRef.current
    setDraft(current.trim() ? `${text}\n\n${current}` : text)
  }, [setDraft])

  const updateOutbox = useCallback((next: OutboxEntry[]) => {
    outboxRef.current = next
    setOutbox(next)
  }, [])

  const socketRef = useRef<ReturnType<typeof useSocket> | null>(null)
  // Texts of acknowledged submits, kept briefly: the worker's rejection event
  // can arrive after the hub's acknowledgement.
  const rejectedTexts = useRef(new Map<string, string>())


  const handleSignals = useCallback((signals: Signal[]) => {
    for (const signal of signals) {
      switch (signal.kind) {
        case 'state': {
          if (signal.streamChanged) withdrawRef.current = null
          // Resend everything still unacknowledged on this fresh link.
          const remaining = outboxRef.current.filter((entry) => entry.actionId !== signal.pendingActionId)
          for (const entry of remaining) {
            socketRef.current?.send({ type: 'submit', text: entry.text, action_id: entry.actionId })
            entry.attempts += 1
          }
          updateOutbox(remaining)
          if (withdrawRef.current) {
            withdrawRef.current = null
            setWithdrawing(false)
          }
          break
        }
        case 'ack':
          if (outboxRef.current.some((entry) => entry.actionId === signal.actionId)) {
            updateOutbox(outboxRef.current.filter((entry) => entry.actionId !== signal.actionId))
          }
          break
        case 'reject': {
          const entry = outboxRef.current.find((item) => item.actionId === signal.actionId)
          if (entry) updateOutbox(outboxRef.current.filter((item) => item !== entry))
          const text = entry?.text ?? rejectedTexts.current.get(signal.actionId)
          rejectedTexts.current.delete(signal.actionId)
          // Another browser's rejection is visible in the transcript already.
          if (text !== undefined) {
            restoreDraft(text)
            toast(signal.error || 'The message was not accepted.')
          }
          break
        }
        case 'withdrawn':
          if (signal.actionId === withdrawRef.current) {
            withdrawRef.current = null
            setWithdrawing(false)
            restoreDraft(signal.text)
          }
          break
        case 'withdraw-failed':
          if (signal.actionId === withdrawRef.current) {
            withdrawRef.current = null
            setWithdrawing(false)
            toast(signal.error || 'The queued message is no longer available.')
          }
          break
        case 'draft':
          setDraft(signal.text)
          break
        case 'error':
          toast(signal.text)
          break
      }
    }
  }, [restoreDraft, setDraft, updateOutbox])

  // Streaming deltas can arrive hundreds of times a second, each as its own
  // socket task; render them at most once per frame. Any other event renders
  // immediately (and carries the pending deltas with it).
  const frame = useRef<number | null>(null)
  const commit = useCallback(() => {
    if (frame.current !== null) {
      cancelAnimationFrame(frame.current)
      frame.current = null
    }
    setView(viewRef.current)
  }, [])
  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
  }, [])

  const onMessage = useCallback((raw: Record<string, unknown>) => {
    const message = raw as ServerEvent
    if (message.type === 'state' && typeof raw.csrf === 'string') setCsrf(raw.csrf)
    const [next, signals] = reduce(viewRef.current, message)
    if (next !== viewRef.current) {
      viewRef.current = next
      if (!BATCHED.has(message.type)) commit()
      else if (frame.current === null) {
        frame.current = requestAnimationFrame(() => {
          frame.current = null
          setView(viewRef.current)
        })
      }
    }
    if (signals.length) handleSignals(signals)
  }, [commit, handleSignals])

  const socket = useSocket({
    url: () => {
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const query = new URLSearchParams({ session: sessionId, after: String(viewRef.current.lastSeq) })
      return `${protocol}://${location.host}/api/ws?${query}`
    },
    onMessage,
    fatalCodes: [4403, 4404],
    onFatal: (code) => {
      if (code === 4404) onGone()
    },
  })
  socketRef.current = socket

  const send = useCallback((action: ClientAction) => socket.send(action), [socket])

  const submit = useCallback((text: string): boolean => {
    const trimmed = text.trim()
    if (!trimmed) return false
    const now = Date.now()
    if (outboxRef.current.some((entry) => entry.text === text && now - entry.sentAt < DOUBLE_SEND_WINDOW)) {
      return false
    }
    const actionId = newActionId()
    if (!send({ type: 'submit', text, action_id: actionId })) {
      toast('Not connected — your message was kept.', 'info')
      return false
    }
    rejectedTexts.current.set(actionId, text)
    window.setTimeout(() => rejectedTexts.current.delete(actionId), 60_000)
    updateOutbox([...outboxRef.current, { actionId, text, sentAt: now, attempts: 1 }])
    return true
  }, [send, updateOutbox])

  const withdraw = useCallback(() => {
    const pending = viewRef.current.pending
    if (!pending || withdrawRef.current) return
    const actionId = newActionId()
    if (!send({ type: 'withdraw_pending', id: pending.id, action_id: actionId })) return
    withdrawRef.current = actionId
    setWithdrawing(true)
  }, [send])

  // Commands from panels (process controls) do not touch the draft.
  const command = useCallback((text: string) => submit(text), [submit])

  const cancel = useCallback(() => {
    send({ type: 'cancel', operation_id: viewRef.current.operationId })
  }, [send])

  const answer = useCallback((answerText: string) => {
    const prompt = viewRef.current.prompt
    if (prompt) send({ type: 'prompt_response', id: prompt.id, answer: answerText })
  }, [send])

  const dismissPrompt = useCallback(() => {
    const prompt = viewRef.current.prompt
    if (prompt) send({ type: 'prompt_cancel', id: prompt.id })
  }, [send])

  useEffect(() => () => store.set(draftKey(sessionId), draftRef.current), [sessionId])

  return {
    view,
    status: socket.status,
    retry: socket.retry,
    draft,
    setDraft,
    outbox,
    withdrawing,
    submit,
    command,
    withdraw,
    cancel,
    answer,
    dismissPrompt,
  }
}

export type SessionHandle = ReturnType<typeof useSession>
