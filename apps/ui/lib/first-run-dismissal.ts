// "Getting started" checklist dismissal. Scoped to the signed-in user and the account being viewed, so
// dismissing it for one user or org never hides it for another in the same browser. The legacy
// un-scoped key is still removed on identity change.

const LEGACY_KEY = "clevis:first-run-checklist-dismissed"
const PREFIX = `${LEGACY_KEY}:`

export function dismissalKey(userId: number, kind: string, login: string): string {
  return `${PREFIX}${userId}:${kind}:${login.toLowerCase()}`
}

export function readDismissed(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1"
  } catch {
    return false
  }
}

export function writeDismissed(key: string): void {
  try {
    localStorage.setItem(key, "1")
  } catch {
    // Storage blocked: the card just comes back next visit.
  }
}

/** Forget every dismissal (legacy and scoped); part of the per-user state cleared on login/logout. */
export function clearDismissals(): void {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key === LEGACY_KEY || key.startsWith(PREFIX)) localStorage.removeItem(key)
    }
  } catch {
    // best-effort; nothing to do if storage is unavailable
  }
}
