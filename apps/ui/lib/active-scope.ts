"use client"

// Shared "which GitHub account am I looking at" state: an org the user belongs to, or their
// personal account. Every /me/* endpoint accepts either as `owner`; only Health & Security is org-only.

import { useCallback, useSyncExternalStore } from "react"

export type ActiveScope = { kind: "org"; login: string } | { kind: "personal"; login: string }

const STORAGE_KEY = "active_scope"
// Legacy "Default organization" key, read as a fallback so existing users keep their selection.
const LEGACY_ORG_KEY = "default_org"
const CHANGE_EVENT = "clevis:active-scope-changed"

function isActiveScope(value: unknown): value is ActiveScope {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  return (v.kind === "org" || v.kind === "personal") && typeof v.login === "string"
}

function parse(raw: string | null): ActiveScope | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (isActiveScope(parsed)) return parsed
  } catch {
    // fall through to null
  }
  return null
}

// Memoized so useSyncExternalStore's getSnapshot is referentially stable (else React's infinite-loop
// warning). Keyed on both keys: STORAGE_KEY alone stays null while the legacy key is in use.
let cachedActiveRaw: string | null = null
let cachedLegacyRaw: string | null = null
let cachedScope: ActiveScope | null = null
let cachedRead = false

function read(): ActiveScope | null {
  if (typeof window === "undefined") return null
  const activeRaw = localStorage.getItem(STORAGE_KEY)
  const legacyRaw = activeRaw === null ? localStorage.getItem(LEGACY_ORG_KEY) : null
  if (cachedRead && activeRaw === cachedActiveRaw && legacyRaw === cachedLegacyRaw) return cachedScope
  cachedRead = true
  cachedActiveRaw = activeRaw
  cachedLegacyRaw = legacyRaw
  cachedScope = activeRaw !== null ? parse(activeRaw) : legacyRaw ? { kind: "org", login: legacyRaw } : null
  return cachedScope
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("storage", callback)
  window.addEventListener(CHANGE_EVENT, callback)
  return () => {
    window.removeEventListener("storage", callback)
    window.removeEventListener(CHANGE_EVENT, callback)
  }
}

function getServerSnapshot(): ActiveScope | null {
  return null
}

export function setActiveScope(scope: ActiveScope): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(scope))
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

// Called on logout so a new user on this browser doesn't start scoped into the previous
// user's org. Clears the legacy key too.
export function clearActiveScope(): void {
  if (typeof window === "undefined") return
  localStorage.removeItem(STORAGE_KEY)
  localStorage.removeItem(LEGACY_ORG_KEY)
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

export function useActiveScope(): { scope: ActiveScope | null; setScope: (scope: ActiveScope) => void } {
  const scope = useSyncExternalStore(subscribe, read, getServerSnapshot)
  const setScope = useCallback((next: ActiveScope) => setActiveScope(next), [])
  return { scope, setScope }
}
