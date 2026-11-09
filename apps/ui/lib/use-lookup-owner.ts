import { useEffect, useState } from "react"

// Pause after the last keystroke in an owner box before it drives network lookups.
export const OWNER_LOOKUP_DEBOUNCE_MS = 400

/** The trimmed `owner`, following it only once typing pauses, so each keystroke doesn't fire a
 * token-resolve, an installation lookup and a history/repo query. */
export function useLookupOwner(owner: string): string {
  const [lookupOwner, setLookupOwner] = useState("")
  useEffect(() => {
    const timer = setTimeout(() => setLookupOwner(owner.trim()), OWNER_LOOKUP_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [owner])
  return lookupOwner
}
