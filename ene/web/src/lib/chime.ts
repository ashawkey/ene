import { store } from './storage'

/** Notification sound played when a session finishes out of view. */

const KEY = 'ene.sound'
const VOLUME = 0.6

// Wind chime: four airy strikes, loosely staggered, each with a faint
// inharmonic overtone (free-bar ratio 2.76) that fades sooner.
const STRIKES: Array<[frequency: number, offset: number]> = [[1760, 0], [2217, 0.11], [1976, 0.26], [2637, 0.38]]
const DURATION = 1.4
const PEAK = 0.18
const PARTIALS: Array<[ratio: number, level: number, decay: number]> = [[1, 1, 1], [2.76, 0.15, 0.4]]

export function loadSound(): boolean {
  return store.get(KEY) !== 'off'
}

export function saveSound(enabled: boolean) {
  store.set(KEY, enabled ? null : 'off')
}

let context: AudioContext | null = null

function audio(): AudioContext | null {
  if (context) return context
  const Ctor = window.AudioContext ?? (window as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    context = new Ctor()
  } catch {
    return null
  }
  return context
}

// A mouse press or a key is a user gesture, but a touch (or pen) becomes one
// only on release: on a phone, pointerdown alone never unlocks audio.
const GESTURES = ['pointerdown', 'pointerup', 'touchend', 'keydown']

/**
 * Browsers only allow audio after a user gesture; resume the context on the
 * first one so a later chime can play while the tab sits in the background.
 */
export function armChime(): () => void {
  const unlock = () => {
    const ctx = audio()
    if (ctx?.state === 'suspended') void ctx.resume().catch(() => {})
    if (!ctx || ctx.state === 'running') remove()
  }
  const remove = () => {
    for (const type of GESTURES) window.removeEventListener(type, unlock, true)
  }
  for (const type of GESTURES) window.addEventListener(type, unlock, true)
  return remove
}

export function playChime() {
  const ctx = audio()
  if (!ctx) return
  if (ctx.state === 'suspended') void ctx.resume().catch(() => {})
  try {
    const now = ctx.currentTime + 0.02
    const master = ctx.createGain()
    master.gain.value = VOLUME
    master.connect(ctx.destination)
    for (const [frequency, offset] of STRIKES) {
      for (const [ratio, level, decay] of PARTIALS) {
        const osc = ctx.createOscillator()
        const envelope = ctx.createGain()
        const start = now + offset
        const end = start + DURATION * decay
        osc.type = 'sine'
        osc.frequency.setValueAtTime(frequency * ratio, start)
        envelope.gain.setValueAtTime(0.0001, start)
        envelope.gain.exponentialRampToValueAtTime(PEAK * level, start + 0.008)
        envelope.gain.exponentialRampToValueAtTime(0.0001, end)
        osc.connect(envelope).connect(master)
        osc.start(start)
        osc.stop(end + 0.05)
      }
    }
  } catch {
    // Sound is a courtesy; never let it break the UI.
  }
}
