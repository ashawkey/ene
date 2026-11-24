import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { deleteAttachment, uploadRaw } from './api'
import {
  MAX_BATCH_FILES,
  MAX_UPLOAD_BYTES,
  batchProblem,
  cancelTransfer,
  dismissTransfer,
  droppedFiles,
  remaining,
  resetTransfers,
  uploadAttachment,
  uploadFiles,
  useTransfers,
} from './transfers'

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  deleteAttachment: vi.fn(async () => ({ ok: true })),
  uploadRaw: vi.fn(),
}))

type Pending = { url: string; progress: (loaded: number, total: number) => void; signal: AbortSignal; resolve: (value: unknown) => void; reject: (exc: unknown) => void }

/** Uploads the fake transport has started, in order. */
function fakeTransport() {
  const started: Pending[] = []
  vi.mocked(uploadRaw).mockImplementation((url, _body, progress, signal) => new Promise((resolve, reject) => {
    started.push({ url, progress, signal, resolve, reject })
    signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')))
  }))
  return started
}

const file = (name: string, size = 10) => new File(['x'.repeat(size)], name)

afterEach(() => {
  act(() => resetTransfers())
  vi.clearAllMocks()
})

describe('transfers', () => {
  it('checks batch limits before uploading', () => {
    expect(batchProblem([])).toBe('Nothing to upload.')
    const many = Array.from({ length: MAX_BATCH_FILES + 1 }, (_, at) => ({ file: file(`${at}`), name: `${at}` }))
    expect(batchProblem(many)).toMatch(/at most 50 files/)
    const big = { file: file('big'), name: 'big' }
    Object.defineProperty(big.file, 'size', { value: MAX_UPLOAD_BYTES + 1 })
    expect(batchProblem([big])).toMatch(/big is over the 100 MB/)
    expect(batchProblem([{ file: file('ok'), name: 'ok' }])).toBeNull()
  })

  it('runs two uploads at a time, reports progress, and settles the batch', async () => {
    const started = fakeTransport()
    const settled = vi.fn()
    const { result } = renderHook(() => useTransfers())
    act(() => uploadFiles('s1', 'sub', ['a', 'b', 'c'].map((name) => ({ file: file(name, 100), name })), new Set(['b']), settled))
    expect(started).toHaveLength(2)
    expect(started[1].url).toContain('overwrite=1')
    expect(result.current.map((item) => [item.name, item.state])).toEqual([
      ['sub/a', 'running'], ['sub/b', 'running'], ['sub/c', 'queued'],
    ])
    vi.spyOn(performance, 'now').mockReturnValue(1e9)
    act(() => started[0].progress(50, 100))
    expect(result.current[0].loaded).toBe(50)
    expect(result.current[0].speed).toBeGreaterThan(0)
    expect(remaining(result.current[0])).not.toBeNull()
    await act(async () => started[0].resolve({ path: 'sub/a' }))
    expect(started).toHaveLength(3)
    expect(result.current[0].state).toBe('done')
    await act(async () => started[1].reject(new Error('Disk full')))
    expect(result.current.find((item) => item.name === 'sub/b')).toMatchObject({ state: 'failed', error: 'Disk full' })
    await act(async () => started[2].resolve({ path: 'sub/c' }))
    expect(settled).toHaveBeenCalledWith(2)
  })

  it('cancels running and queued uploads without leaving entries', async () => {
    const started = fakeTransport()
    const settled = vi.fn()
    const { result } = renderHook(() => useTransfers())
    act(() => uploadFiles('s1', '', ['a', 'b', 'c'].map((name) => ({ file: file(name), name })), new Set(), settled))
    const [running, , queued] = result.current
    act(() => cancelTransfer(queued.id))
    await act(async () => cancelTransfer(running.id))
    expect(started[0].signal.aborted).toBe(true)
    expect(result.current.map((item) => item.name)).toEqual(['b'])
    await act(async () => started[1].resolve({ path: 'b' }))
    expect(settled).toHaveBeenCalledWith(1)
  })

  it('deletes a dismissed attachment from the hub', async () => {
    const started = fakeTransport()
    const { result } = renderHook(() => useTransfers())
    let id = 0
    act(() => {
      id = uploadAttachment('s1', file('shot.png'))
    })
    expect(started[0].url).toBe('/api/sessions/s1/attachments?name=shot.png')
    await act(async () => started[0].resolve({ path: '.ene/uploads/c/t/shot.png' }))
    act(() => dismissTransfer(id))
    expect(deleteAttachment).toHaveBeenCalledWith('s1', '.ene/uploads/c/t/shot.png')
    expect(result.current).toEqual([])
  })

  it('walks dropped folders into relative names', async () => {
    const fileEntry = (name: string) => ({ isFile: true, isDirectory: false, name, file: (ok: (value: File) => void) => ok(file(name)) })
    const pages = [[fileEntry('a.txt'), fileEntry('b.txt')], []]
    const folder = {
      isFile: false,
      isDirectory: true,
      name: 'docs',
      createReader: () => ({ readEntries: (ok: (value: unknown[]) => void) => ok(pages.shift() ?? []) }),
    }
    const data = {
      items: [{ kind: 'file', webkitGetAsEntry: () => folder }, { kind: 'file', webkitGetAsEntry: () => fileEntry('top.md') }],
      files: [],
    } as unknown as DataTransfer
    expect((await droppedFiles(data)).map((item) => item.name)).toEqual(['docs/a.txt', 'docs/b.txt', 'top.md'])
  })
})
