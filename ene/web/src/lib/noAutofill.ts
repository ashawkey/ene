/**
 * Spread on inputs that never hold credentials. Password-manager extensions
 * (LastPass in particular) otherwise inject overlay frames next to them; when
 * such a frame fails to load it can sit invisibly over the UI and swallow clicks.
 */
export const NO_AUTOFILL = {
  autoComplete: 'off',
  'data-lpignore': 'true',
  'data-1p-ignore': 'true',
  'data-bwignore': 'true',
  'data-form-type': 'other',
} as const
