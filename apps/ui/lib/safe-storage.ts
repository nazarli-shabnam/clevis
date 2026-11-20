// localStorage that never throws. With storage blocked (some private modes, strict site-data settings, embedded
// views) the accessor itself throws, which used to take down auth bootstrap and every request header. Reads and
// writes fall back to memory for the life of the page, so the session still works (it just does not persist).

const memory = new Map<string, string>()

export const safeStorage = {
  getItem(key: string): string | null {
    let stored: string | null = null
    try {
      stored = localStorage.getItem(key)
    } catch {
      // blocked: fall through to the in-memory copy
    }
    return stored ?? memory.get(key) ?? null
  },
  setItem(key: string, value: string): void {
    try {
      localStorage.setItem(key, value)
      memory.delete(key)
    } catch {
      memory.set(key, value)
    }
  },
  removeItem(key: string): void {
    memory.delete(key)
    try {
      localStorage.removeItem(key)
    } catch {
      // blocked: nothing persisted to remove
    }
  },
}
