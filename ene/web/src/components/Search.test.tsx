import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { searchText } from '../lib/api'
import type { SearchResult } from '../lib/protocol'
import { TextSearch } from './Search'

vi.mock('../lib/api', () => ({
  searchText: vi.fn(),
  errorMessage: (_exc: unknown, fallback: string) => fallback,
}))

const result = (files: SearchResult['files'] = []): SearchResult => ({
  root: '/work',
  files,
  match_count: files.reduce((sum, file) => sum + file.matches.length, 0),
  file_count: files.length,
  truncated: false,
  reason: null,
  per_file_limit: 50,
})

const Search = () => <TextSearch sessionId="s1" onOpen={() => {}} onMention={() => {}} refreshKey={0} focusSignal={0} />

beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(searchText).mockResolvedValue(result())
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
  localStorage.clear()
})

describe('text search', () => {
  it('restores the query and its options after a remount', async () => {
    const first = render(<Search />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Search in files' }), { target: { value: 'deploy' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use regular expression' }))
    fireEvent.click(screen.getByRole('button', { name: 'Match case' }))
    fireEvent.click(screen.getByRole('button', { name: 'Files to include or exclude' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Files to include' }), { target: { value: '*.py' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    const options = { regex: true, case: true, word: false, include: '*.py', exclude: '' }
    expect(searchText).toHaveBeenLastCalledWith('s1', 'deploy', options)
    first.unmount()

    vi.mocked(searchText).mockClear()
    render(<Search />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByRole('textbox', { name: 'Search in files' })).toHaveValue('deploy')
    expect(screen.getByRole('button', { name: 'Use regular expression' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Match case' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('textbox', { name: 'Files to include' })).toHaveValue('*.py')
    expect(searchText).toHaveBeenCalledExactlyOnceWith('s1', 'deploy', options)
  })

  it('highlights hub hits after characters outside the BMP', async () => {
    // The hub counts code points: the rocket is one character, but two UTF-16 units.
    vi.mocked(searchText).mockResolvedValue(result([
      { path: 'app.py', capped: false, matches: [{ line: 3, text: 'msg = "🚀 deploy now"', ranges: [[9, 15]], clipped: false }] },
    ]))
    const { container } = render(<Search />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Search in files' }), { target: { value: 'deploy' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(300) })
    expect([...container.querySelectorAll('.search-line mark')].map((mark) => mark.textContent)).toEqual(['deploy'])
  })
})
