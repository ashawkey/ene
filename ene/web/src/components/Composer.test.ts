import { describe, expect, it } from 'vitest'

import { shellHistory, shellSubmission } from './Composer'

describe('shell mode', () => {
  it('prefixes submissions with ! once', () => {
    expect(shellSubmission('ls -la')).toBe('!ls -la')
    expect(shellSubmission('!git status')).toBe('!git status')
  })

  it('recalls only earlier shell commands, without the prefix', () => {
    expect(shellHistory(['hello', '!ls', '/clear', '!', '!git diff'])).toEqual(['ls', 'git diff'])
  })
})
