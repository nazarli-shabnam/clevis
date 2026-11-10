// When the user last looked at Activity, per account. The sidebar's unread badge counts the active account's
// events newer than this, so one account's stamp must never mark another account's events as read.

const LEGACY_KEY = "activity_last_seen_at"
const PREFIX = `${LEGACY_KEY}:`

export function activityLastSeenKey(kind: string, login: string): string {
  return `${PREFIX}${kind}:${login.toLowerCase()}`
}

export function readActivityLastSeen(kind: string, login: string): string | null {
  try {
    return localStorage.getItem(activityLastSeenKey(kind, login))
  } catch {
    return null
  }
}

export function writeActivityLastSeen(kind: string, login: string): void {
  try {
    localStorage.setItem(activityLastSeenKey(kind, login), new Date().toISOString())
  } catch {
    // Storage blocked (private mode): the badge simply can't clear.
  }
}

/** Forget every account's stamp (and the old un-scoped one); part of the per-user state cleared on login/logout. */
export function clearActivityLastSeen(): void {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key === LEGACY_KEY || key.startsWith(PREFIX)) localStorage.removeItem(key)
    }
  } catch {
    // best-effort; nothing to do if storage is unavailable
  }
}
