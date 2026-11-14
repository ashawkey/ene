import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Prompt } from '../lib/protocol'
import { PromptSheet } from './PromptSheet'
import { Modal } from './ui'

const question: Prompt = { id: 'p1', kind: 'select', message: 'Proceed?', choices: ['yes', 'no'], default: '' }

function setup({ active = true, modal = false } = {}) {
  const onAnswer = vi.fn()
  const onCancel = vi.fn()
  const onClose = vi.fn()
  render(
    <>
      <PromptSheet prompt={question} active={active} connected onAnswer={onAnswer} onCancel={onCancel} />
      <div className="composer"><textarea aria-label="Message" defaultValue="" /></div>
      <input aria-label="Find in conversation" defaultValue="" />
      {modal ? <Modal onClose={onClose} label="File"><p>file</p></Modal> : null}
    </>,
  )
  return { onAnswer, onCancel, onClose }
}

describe('PromptSheet keys', () => {
  it('answers with a number key from the empty composer and dismisses with Escape', () => {
    const { onAnswer, onCancel } = setup()
    const composer = screen.getByLabelText('Message')
    fireEvent.keyDown(composer, { key: '2' })
    expect(onAnswer).toHaveBeenCalledWith('no')
    fireEvent.keyDown(composer, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('ignores keys while its session is not in view', () => {
    // Another session's question must not take this pane's keys.
    const { onAnswer, onCancel } = setup({ active: false })
    const composer = screen.getByLabelText('Message')
    expect(fireEvent.keyDown(composer, { key: '2' })).toBe(true)
    expect(fireEvent.keyDown(composer, { key: 'Escape' })).toBe(true)
    expect(onAnswer).not.toHaveBeenCalled()
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('leaves Escape and digits to an open dialog', () => {
    const { onAnswer, onCancel, onClose } = setup({ modal: true })
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onCancel).not.toHaveBeenCalled()
    fireEvent.keyDown(document.body, { key: '1' })
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('can answer again when the link comes back with the question still open', () => {
    // An answer lost with a dropped link must not leave the sheet disabled.
    const onAnswer = vi.fn()
    const sheet = (connected: boolean) => (
      <PromptSheet prompt={question} active connected={connected} onAnswer={onAnswer} onCancel={() => undefined} />
    )
    const { rerender } = render(sheet(true))
    fireEvent.click(screen.getByText('yes'))
    expect(screen.getByText('yes').closest('button')).toBeDisabled()
    rerender(sheet(false))
    rerender(sheet(true))
    expect(screen.getByText('yes').closest('button')).toBeEnabled()
  })

  it('stays answerable when the answer is refused', () => {
    const onAnswer = vi.fn(() => false)
    render(<PromptSheet prompt={question} active connected onAnswer={onAnswer} onCancel={() => undefined} />)
    fireEvent.click(screen.getByText('no'))
    expect(onAnswer).toHaveBeenCalledWith('no')
    expect(screen.getByText('no').closest('button')).toBeEnabled()
  })

  it('leaves other fields their own keys', () => {
    const { onAnswer, onCancel } = setup()
    const find = screen.getByLabelText('Find in conversation')
    expect(fireEvent.keyDown(find, { key: '1' })).toBe(true)
    expect(fireEvent.keyDown(find, { key: 'Escape' })).toBe(true)
    expect(onAnswer).not.toHaveBeenCalled()
    expect(onCancel).not.toHaveBeenCalled()
  })
})
