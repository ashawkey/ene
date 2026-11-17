import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, fileDiff, fileLog, findFiles, listChanges, listFiles, readFile } from '../lib/api'
import type { DirEntry, DirListing, GitChange } from '../lib/protocol'
import { FileBrowser, FileViewer } from './Files'
import { Toasts, copyText } from './ui'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  fileDiff: vi.fn(),
  fileLog: vi.fn(),
  findFiles: vi.fn(),
  listChanges: vi.fn(),
  listFiles: vi.fn(),
  readFile: vi.fn(),
}))

vi.mock('./ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./ui')>()),
  copyText: vi.fn(async () => true),
}))

const entry = (name: string, type: 'dir' | 'file', extra: Partial<DirEntry> = {}): DirEntry => (
  { name, type, size: 0, mtime: 0, hidden: false, symlink: false, ...extra }
)
const folder = (path: string, entries: DirEntry[]): DirListing => ({ root: '/work', path, entries, truncated: false })

const FOLDERS: Record<string, DirListing> = {
  '': folder('', [entry('src', 'dir'), entry('README.md', 'file')]),
  src: folder('src', [entry('escape', 'dir', { symlink: true }), entry('main.py', 'file')]),
}

function serveFolders(failures: Record<string, ApiError> = {}) {
  vi.mocked(listFiles).mockImplementation(async (_session, path) => {
    if (failures[path]) throw failures[path]
    if (FOLDERS[path]) return FOLDERS[path]
    throw new ApiError('Path not found.', 404)
  })
}

const Browser = () => (
  <>
    <FileBrowser sessionId="s1" onOpen={() => {}} onMention={() => {}} refreshKey={0} />
    <Toasts />
  </>
)
const crumbs = () => within(screen.getByRole('navigation', { name: 'Folder path' }))

beforeEach(() => {
  vi.mocked(listChanges).mockResolvedValue({ git: false, branch: '', changes: [] })
})

afterEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

describe('file browser', () => {
  it('folds a folder back when it cannot be opened', async () => {
    serveFolders({ 'src/escape': new ApiError('Path is outside the workspace.', 403) })
    render(<Browser />)
    fireEvent.click(await screen.findByText('src'))
    fireEvent.click(await screen.findByText('escape'))
    expect(await screen.findByText('Path is outside the workspace.')).toBeInTheDocument()
    await act(async () => {})
    expect(screen.getByText('escape').closest('button')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('main.py')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('ene.files.open.s1') ?? '[]')).toEqual(['src'])
  })

  it('expands folders in place, remembers them, and browses into one from its menu', async () => {
    serveFolders()
    const { unmount } = render(<Browser />)
    const src = await screen.findByText('src')
    expect(src.closest('button')).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(src)
    expect(await screen.findByText('main.py')).toBeInTheDocument()
    expect(src.closest('button')).toHaveAttribute('aria-expanded', 'true')
    unmount()

    render(<Browser />)
    expect(await screen.findByText('main.py')).toBeInTheDocument()
    fireEvent.contextMenu(screen.getByText('src'))
    fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: 'Browse into folder' }))
    expect(within(await screen.findByRole('navigation', { name: 'Folder path' })).getByRole('button', { name: 'src' })).toBeInTheDocument()
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('moves through rows with the arrow keys and opens folders with Right', async () => {
    serveFolders()
    render(<Browser />)
    await screen.findByText('src')
    const finder = screen.getByRole('textbox', { name: /find files in the workspace/ })
    fireEvent.keyDown(finder, { key: 'ArrowDown' })
    const srcRow = screen.getByText('src').closest('button')!
    expect(srcRow).toHaveFocus()
    fireEvent.keyDown(srcRow, { key: 'ArrowRight' })
    expect(await screen.findByText('main.py')).toBeInTheDocument()
    fireEvent.keyDown(srcRow, { key: 'ArrowDown' })
    const escape = screen.getByText('escape').closest('button')!
    expect(escape).toHaveFocus()
    fireEvent.keyDown(escape, { key: 'ArrowLeft' })
    expect(srcRow).toHaveFocus()
    fireEvent.keyDown(srcRow, { key: 'ArrowLeft' })
    await waitFor(() => expect(screen.queryByText('main.py')).toBeNull())
  })

  it('offers file actions from the context menu', async () => {
    serveFolders()
    const onAsk = vi.fn()
    const onTerminal = vi.fn()
    const onOpen = vi.fn()
    render(<FileBrowser sessionId="s1" onOpen={onOpen} onMention={() => {}} onAsk={onAsk} onTerminal={onTerminal} refreshKey={0} />)
    fireEvent.click(await screen.findByText('src'))
    const pick = (name: string) => {
      fireEvent.contextMenu(screen.getByText('main.py'))
      fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name }))
    }
    await screen.findByText('main.py')
    pick('Copy relative path')
    expect(copyText).toHaveBeenCalledWith('src/main.py')
    pick('Copy absolute path')
    expect(copyText).toHaveBeenCalledWith('/work/src/main.py')
    pick('Open in terminal')
    expect(onTerminal).toHaveBeenCalledWith('src')
    pick('Ask to explain')
    expect(onAsk).toHaveBeenCalledWith('Explain @src/main.py ')
    pick('Open')
    expect(onOpen).toHaveBeenCalledWith('src/main.py')
    // Not a Git repo here: no history, and no changes to review.
    fireEvent.contextMenu(screen.getByText('main.py'))
    const menu = within(screen.getByRole('menu'))
    expect(menu.queryByRole('menuitem', { name: 'File history' })).toBeNull()
    expect(menu.queryByRole('menuitem', { name: 'Ask to review changes' })).toBeNull()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('groups changed files and shows their line counts', async () => {
    serveFolders()
    const change = (path: string, status: GitChange['status'], staged: boolean, unstaged: boolean, added: number | null, removed: number | null): GitChange => (
      { path, status, staged, unstaged, added, removed }
    )
    vi.mocked(listChanges).mockResolvedValue({
      git: true,
      branch: 'main',
      upstream: 'origin/main',
      ahead: 2,
      behind: 0,
      changes: [
        change('src/main.py', 'modified', true, false, 3, 1),
        change('README.md', 'modified', true, true, 5, 0),
        change('notes.txt', 'untracked', false, true, 7, 0),
      ],
    })
    render(<Browser />)
    fireEvent.click(await screen.findByRole('tab', { name: 'Changes (3)' }))
    const group = (name: string) => within(screen.getByRole('group', { name }))
    expect(group('Staged').getByText('main.py')).toBeInTheDocument()
    expect(group('Staged').getByText('+3')).toBeInTheDocument()
    expect(group('Staged').getByText('−1')).toBeInTheDocument()
    expect(group('Changes').getByText('README.md').closest('button')).toHaveAttribute('title', 'Partially staged')
    expect(group('Untracked').getByText('+7')).toBeInTheDocument()
    expect(screen.getByText('↑2')).toBeInTheDocument()
    expect(screen.queryByText(/↓/)).toBeNull()
  })

  it('opens the first match for the query typed when Enter is pressed', async () => {
    serveFolders()
    const hit = (path: string) => ({ path, type: 'file' as const })
    const answers: Record<string, string[]> = {
      app: ['src/application/x.py', 'web/app.py'],
      'app.py': ['web/app.py', 'src/application/x.py'],
    }
    vi.mocked(findFiles).mockImplementation(async (_session, query) => ({ results: (answers[query] ?? []).map(hit), truncated: false }))
    const onOpen = vi.fn()
    render(<FileBrowser sessionId="s1" onOpen={onOpen} onMention={() => {}} refreshKey={0} />)
    await screen.findByText('src')
    const finder = screen.getByRole('textbox', { name: /find files in the workspace/ })
    fireEvent.change(finder, { target: { value: 'app' } })
    expect(await screen.findByText('Workspace')).toBeInTheDocument()

    // Enter before the debounced lookup for "app.py" has even started.
    fireEvent.change(finder, { target: { value: 'app.py' } })
    fireEvent.keyDown(finder, { key: 'Enter' })
    await waitFor(() => expect(onOpen).toHaveBeenCalled())
    expect(onOpen).toHaveBeenCalledExactlyOnceWith('web/app.py', undefined)
  })

  it('highlights fuzzy path matches after characters outside the BMP', async () => {
    serveFolders()
    vi.mocked(findFiles).mockResolvedValue({ results: [{ path: '🚀/docs/x.md', type: 'file' }], truncated: false })
    const { container } = render(<FileBrowser sessionId="s1" onOpen={() => {}} onMention={() => {}} refreshKey={0} />)
    await screen.findByText('src')
    fireEvent.change(screen.getByRole('textbox', { name: /find files in the workspace/ }), { target: { value: 'doc' } })
    await screen.findByText('Workspace')
    expect([...container.querySelectorAll('.file-list mark')].map((mark) => mark.textContent)).toEqual(['doc'])
  })

  it('falls back to the root when the remembered folder is gone', async () => {
    localStorage.setItem('ene.files.s1', 'deleted')
    serveFolders()
    render(<Browser />)
    expect(await screen.findByText('README.md')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Folder path' })).toBeNull()
    expect(document.querySelector('.panel-error')).toBeNull()
    expect(listFiles).toHaveBeenCalledWith('s1', 'deleted')
  })
})

const serveText = (path: string, text: string) => {
  vi.mocked(readFile).mockResolvedValue({ path, name: path, size: text.length, mtime: 0, kind: 'text', text })
  vi.mocked(fileDiff).mockResolvedValue({ path, status: 'unchanged', old: text, new: text, binary: false, too_large: false })
}

describe('file viewer', () => {
  it('mentions the lines picked in the gutter', async () => {
    serveText('a.py', 'one\ntwo\nthree\nfour\n')
    const onMention = vi.fn()
    const { container } = render(<FileViewer sessionId="s1" path="a.py" onClose={() => {}} onMention={onMention} />)
    await waitFor(() => expect(container.querySelector('.code-view')).not.toBeNull())
    const gutter = (line: number) => container.querySelector(`.code-line[data-line="${line}"] .ln`)!
    fireEvent.mouseDown(gutter(2))
    fireEvent.mouseDown(gutter(4), { shiftKey: true })
    expect(screen.getByText('Lines 2–4')).toBeInTheDocument()
    expect(container.querySelectorAll('.code-line.selected')).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'Mention' }))
    expect(onMention).toHaveBeenCalledWith('a.py:2-4')
    fireEvent.mouseDown(gutter(1))
    expect(screen.getByText('Line 1')).toBeInTheDocument()
  })

  it('finds text and jumps to a line', async () => {
    serveText('a.py', 'alpha\nbeta\nalphabet\n')
    const { container } = render(<FileViewer sessionId="s1" path="a.py" onClose={() => {}} onMention={() => {}} />)
    await waitFor(() => expect(container.querySelector('.code-view')).not.toBeNull())
    fireEvent.keyDown(window, { key: 'f', ctrlKey: true, metaKey: true })
    const input = await screen.findByRole('textbox', { name: 'Find in file' })
    fireEvent.change(input, { target: { value: 'alpha' } })
    expect(await screen.findByText('1/2')).toBeInTheDocument()
    expect(container.querySelector('.code-line.current')).toHaveAttribute('data-line', '1')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(await screen.findByText('2/2')).toBeInTheDocument()
    expect(container.querySelector('.code-line.current')).toHaveAttribute('data-line', '3')
    fireEvent.change(input, { target: { value: ':2' } })
    await waitFor(() => expect(container.querySelector('.code-line.target')).toHaveAttribute('data-line', '2'))
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByRole('textbox', { name: 'Find in file' })).toBeNull()
  })

  it('says when the hub is too old to have file history', async () => {
    serveText('b.py', 'one\n')
    vi.mocked(fileLog).mockRejectedValue(new ApiError('Not Found', 404))
    render(<FileViewer sessionId="s1" path="b.py" view="history" onClose={() => {}} onMention={() => {}} />)
    expect(await screen.findByText(/Restart `ene hub`/)).toBeInTheDocument()
  })

  it('switches views with labeled tabs and keeps file actions in a menu', async () => {
    serveText('README.md', '# Title\n')
    const { container } = render(<FileViewer sessionId="s1" path="README.md" onClose={() => {}} onMention={() => {}} />)
    await waitFor(() => expect(container.querySelector('.viewer-markdown h1')).not.toBeNull())
    const tabs = within(screen.getByRole('tablist', { name: 'View' }))
    expect(tabs.getAllByRole('tab').map((tab) => tab.getAttribute('aria-label'))).toEqual(['Preview', 'Source', 'History'])
    expect(tabs.getByRole('tab', { name: 'Preview' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(tabs.getByRole('tab', { name: 'Source' }))
    expect(container.querySelector('.code-view')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Wrap long lines/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const menu = within(screen.getByRole('menu'))
    expect(menu.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Copy path', 'Copy contents', 'Download', 'Reload'])
    fireEvent.click(menu.getByRole('menuitem', { name: 'Copy path' }))
    expect(copyText).toHaveBeenCalledWith('README.md')
  })

  it('finds text in rendered Markdown without leaving it', async () => {
    serveText('README.md', '# Setup\n\nRun **set**up first.\n')
    const { container } = render(<FileViewer sessionId="s1" path="README.md" onClose={() => {}} onMention={() => {}} />)
    await waitFor(() => expect(container.querySelector('.viewer-markdown h1')).not.toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /Find in file/ }))
    const input = await screen.findByRole('textbox', { name: 'Find in file' })
    fireEvent.change(input, { target: { value: 'setup' } })
    expect(await screen.findByText('1/2')).toBeInTheDocument()
    expect(container.querySelector('.viewer-markdown')).not.toBeNull()
    fireEvent.change(input, { target: { value: ':3' } })
    await waitFor(() => expect(container.querySelector('.code-line.target')).toHaveAttribute('data-line', '3'))
  })

  it('lists the file history and shows a commit diff', async () => {
    serveText('b.py', 'one\ntwo\n')
    vi.mocked(fileLog).mockResolvedValue({
      path: 'b.py',
      truncated: false,
      commits: [
        { hash: 'f'.repeat(40), short: 'fffffff', author: 'Ann', time: 0, subject: 'extend b', path: 'b.py' },
        { hash: 'e'.repeat(40), short: 'eeeeeee', author: 'Ann', time: 0, subject: 'add a', path: 'a.py' },
        { hash: 'd'.repeat(40), short: 'ddddddd', author: 'Bo', time: 0, subject: 'from elsewhere', path: null },
      ],
    })
    const { container } = render(<FileViewer sessionId="s1" path="b.py" view="history" onClose={() => {}} onMention={() => {}} />)
    expect(await screen.findByText('extend b')).toBeInTheDocument()
    expect(screen.getByText('from elsewhere').closest('button')).toBeDisabled()
    vi.mocked(fileDiff).mockResolvedValue({ path: 'a.py', status: 'added', old: '', new: 'one\n', binary: false, too_large: false })
    fireEvent.click(screen.getByText('add a'))
    await waitFor(() => expect(container.querySelector('.split-diff')).not.toBeNull())
    expect(fileDiff).toHaveBeenLastCalledWith('s1', 'a.py', 'e'.repeat(40))
    fireEvent.click(screen.getByRole('button', { name: 'Back to history' }))
    expect(screen.getByText('extend b')).toBeInTheDocument()
  })

  it('shows the file when a changed file has no content diff', async () => {
    // A mode-only change (chmod +x) is listed as modified, but its content is unchanged.
    const text = 'echo hi\n'
    vi.mocked(readFile).mockResolvedValue({ path: 'run.sh', name: 'run.sh', size: text.length, mtime: 0, kind: 'text', text })
    vi.mocked(fileDiff).mockResolvedValue({ path: 'run.sh', status: 'unchanged', old: text, new: text, binary: false, too_large: false })
    const { container } = render(<FileViewer sessionId="s1" path="run.sh" changed onClose={() => {}} onMention={() => {}} />)
    await waitFor(() => expect(container.querySelector('.code-view')).not.toBeNull())
    expect(container.querySelector('.split-diff')).toBeNull()
    expect(screen.getByText(/2 lines/)).toBeInTheDocument()
  })
})
