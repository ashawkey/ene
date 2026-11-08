import { store } from './storage'

export type ThemeChoice = 'system' | 'light' | 'dark'
const KEY = 'ene.theme'

export function loadTheme(): ThemeChoice {
  const value = store.get(KEY)
  return value === 'light' || value === 'dark' ? value : 'system'
}

export function applyTheme(choice: ThemeChoice) {
  store.set(KEY, choice === 'system' ? null : choice)
  const root = document.documentElement
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', choice)
}

export const nextTheme = (choice: ThemeChoice): ThemeChoice =>
  choice === 'system' ? 'light' : choice === 'light' ? 'dark' : 'system'
