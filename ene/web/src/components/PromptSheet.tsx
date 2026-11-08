import { CircleHelp, CornerDownLeft, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { Prompt } from '../lib/protocol'
import { Markdown } from './Markdown'
import { IconButton } from './ui'

/** An agent question. Answers are idempotent per prompt id on the worker. */
export function PromptSheet({
  prompt,
  connected,
  onAnswer,
  onCancel,
}: {
  prompt: Prompt
  connected: boolean
  onAnswer: (answer: string) => void
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

  const answer = (value: string) => {
    if (sent || !connected) return
    setSent(true)
    onAnswer(value)
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (event.key === 'Escape') {
        // Captured first so the composer does not also stop the round.
        event.preventDefault()
        event.stopPropagation()
        onCancel()
        return
      }
      if (prompt.kind !== 'select' || event.metaKey || event.ctrlKey || event.altKey) return
      // Typing in a field keeps its digits, unless the composer is empty.
      const field = target as HTMLTextAreaElement | HTMLInputElement | null
      if ((target?.tagName === 'TEXTAREA' || target?.tagName === 'INPUT') && field?.value) return
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
