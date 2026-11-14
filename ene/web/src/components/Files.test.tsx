import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, fileDiff, findFiles, listChanges, listFiles, readFile } from '../lib/api'
import type { DirEntry, DirListing } from '../lib/protocol'
import { FileBrowser, FileViewer } from './Files'
import { Toasts } from './ui'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  fileDiff: vi.fn(),
  findFiles: vi.fn(),
  listChanges: vi.fn(),
  listFiles: vi.fn(),
  readFile: vi.fn(),
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
  it('stays in the current folder when a folder cannot be opened', async () => {
    serveFolders({ 'src/escape': new ApiError('Path is outside the workspace.', 403) })
    render(<Browser />)
    fireEvent.click(await screen.findByText('src'))
    fireEvent.click(await screen.findByText('escape'))
    expect(await screen.findByText('Path is outside the workspace.')).toBeInTheDocument()
    await act(async () => {})
    expect(crumbs().getByRole('button', { name: 'src' })).toBeInTheDocument()
    expect(screen.getByText('main.py')).toBeInTheDocument()
    expect(localStorage.getItem('ene.files.s1')).toBe('src')
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
    expect(crumbs().queryAllByRole('button')).toHaveLength(1)
    expect(document.querySelector('.panel-error')).toBeNull()
    expect(listFiles).toHaveBeenCalledWith('s1', 'deleted')
  })
})

describe('file viewer', () => {
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
