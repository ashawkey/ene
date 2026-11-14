import { CircleHelp, CornerDownLeft, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { Prompt } from '../lib/protocol'
import { inTerminal } from '../lib/terminalSocket'
import { Markdown } from './Markdown'
import { IconButton, modalOpen } from './ui'
import { NO_AUTOFILL } from '../lib/noAutofill'

const isField = (target: HTMLElement | null) =>
  target !== null && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' ||
    target.isContentEditable)

/**
 * An agent question. Answers are idempotent per prompt id on the worker.
 *
 * Keys go to it only while its session is *active*, no dialog is open, and
 * focus is in the composer, the sheet, or outside any text field: other
 * fields (find bar, filters, session search) keep their own Escape and digits.
 */
export function PromptSheet({
  prompt,
  active,
  connected,
  onAnswer,
  onCancel,
}: {
  prompt: Prompt
  active: boolean
  connected: boolean
  /** False when the answer was not sent; the sheet then stays answerable. */
  onAnswer: (answer: string) => boolean | void
  onCancel: () => void
}) {
  const [text, setText] = useState(prompt.default)
  const [sent, setSent] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    setText(prompt.default)
    setSent(false)
    input.current?.focus()
  }, [prompt.id, prompt.default])

  // Still open after a reconnect means the answer never arrived; allow
  // another (answers are idempotent per prompt id).
  useEffect(() => {
    if (connected) setSent(false)
  }, [connected])

  const answer = (value: string) => {
    if (sent || !connected) return
    if (onAnswer(value) !== false) setSent(true)
  }

  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (inTerminal(target) || modalOpen()) return
      const field = isField(target)
      if (field && !target!.closest('.composer, .prompt-sheet')) return
      if (event.key === 'Escape') {
        // Captured first so the composer does not also stop the round.
        event.preventDefault()
        event.stopPropagation()
        onCancel()
        return
      }
      if (prompt.kind !== 'select' || event.metaKey || event.ctrlKey || event.altKey) return
      // The composer keeps its digits once it holds text.
      if (field && (target as HTMLTextAreaElement).value) return
      const number = Number(event.key)
      if (Number.isInteger(number) && number >= 1 && number <= prompt.choices.length) {
        event.preventDefault()
        event.stopPropagation()
        answer(prompt.choices[number - 1])
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  return (
    <div className="prompt-sheet glass" role="dialog" aria-label="Agent question">
      <div className="prompt-head">
        <CircleHelp size={16} />
        <span className="grow">Input needed</span>
        <IconButton icon={X} label="Dismiss (Esc)" size="sm" onClick={onCancel} disabled={!connected} />
      </div>
      <div className="prompt-message"><Markdown text={prompt.message} /></div>
      {prompt.kind === 'select' ? (
        <div className="prompt-choices">
          {prompt.choices.map((choice, index) => (
            <button
              type="button"
              key={`${index}-${choice}`}
              className={`choice ${choice === prompt.default ? 'default' : ''}`}
              disabled={sent || !connected}
              onClick={() => answer(choice)}
            >
              <span className="choice-key">{index + 1}</span>
              <span>{choice}</span>
            </button>
          ))}
        </div>
      ) : (
        <form
          className="prompt-text"
          onSubmit={(event) => {
            event.preventDefault()
            answer(text)
          }}
        >
          <textarea
            {...NO_AUTOFILL}
            ref={input}
            value={text}
            rows={2}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                answer(text)
              }
            }}
          />
          <IconButton icon={CornerDownLeft} label="Answer" tone="accent" type="submit" disabled={sent || !connected} />
        </form>
      )}
    </div>
  )
}
