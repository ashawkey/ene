/** localStorage that never throws (private mode, blocked storage, quotas). */
export const store = {
  get(key: string): string | null {
    try {
      return window.localStorage.getItem(key)
    } catch {
      return null
    }
  },
  set(key: string, value: string | null) {
    try {
      if (value === null || value === '') window.localStorage.removeItem(key)
      else window.localStorage.setItem(key, value)
    } catch {
      // Ignore: drafts and preferences are conveniences.
    }
  },
}
