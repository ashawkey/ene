/** Apple devices, where page shortcuts use Cmd: Ctrl+letter edits text there. */
export const isApple = () => typeof navigator !== 'undefined' && /Mac|iP(hone|ad)/.test(navigator.platform)

/** Modifier prefix for shortcut hints: ⌘ on Apple devices, Ctrl+ elsewhere. */
export const MOD_KEY = isApple() ? '⌘' : 'Ctrl+'

/** Whether *event* holds the page-shortcut modifier (Cmd on Apple devices, Ctrl elsewhere). */
export const hasShortcutModifier = (event: { metaKey: boolean; ctrlKey: boolean }) =>
  isApple() ? event.metaKey : event.ctrlKey
