import { afterEach, describe, expect, it, vi } from 'vitest'

describe('chime', () => {
  afterEach(() => {
    window.localStorage.clear()
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  it('is on by default and remembers being turned off', async () => {
    const { loadSound, saveSound } = await import('./chime')
    expect(loadSound()).toBe(true)
    saveSound(false)
    expect(loadSound()).toBe(false)
    saveSound(true)
    expect(loadSound()).toBe(true)
  })

  it('does nothing without Web Audio', async () => {
    vi.stubGlobal('AudioContext', undefined)
    const { playChime } = await import('./chime')
    expect(() => playChime()).not.toThrow()
  })

  it('schedules the wind chime strikes', async () => {
    const started: number[] = []
    const param = () => ({ value: 0, setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() })
    const node = () => ({ gain: param(), connect: vi.fn((next) => next) })
    class FakeContext {
      state = 'running'
      currentTime = 0
      destination = {}
      resume = vi.fn(async () => {})
      createGain = node
      createOscillator = () => ({
        ...node(),
        type: '',
        frequency: param(),
        start: (at: number) => started.push(at),
        stop: vi.fn(),
      })
    }
    vi.stubGlobal('AudioContext', FakeContext)
    const { playChime } = await import('./chime')
    playChime()
    // Four strikes, each a fundamental plus one overtone.
    expect(started).toHaveLength(8)
  })
})
