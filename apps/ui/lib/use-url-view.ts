"use client"

import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "next/navigation"
import type { UrlViewCodec } from "@/lib/list-views"

/**
 * Keeps a list page's view (filters, sort, search) in the query string so it survives a reload and can be
 * shared, the way the Repositories page does. The URL is read after mount (not during render) to avoid a
 * hydration mismatch, then mirrored back with replaceState (no history entries). A change to the search
 * params (Back/Forward, or a link to the same page) is applied through `apply`; a query this hook wrote
 * itself is skipped, so the lagging params update that follows our own write can't overwrite newer state.
 * Query keys the codec does not own (e.g. `roster`, `job_id`) are preserved.
 *
 * Must render under a Suspense boundary (useSearchParams).
 */
export function useUrlView<T>(view: T, codec: UrlViewCodec<T>, apply: (view: T) => void): void {
  const [restored, setRestored] = useState(false)
  const lastWritten = useRef<string | null>(null)
  const applyRef = useRef(apply)
  useEffect(() => {
    applyRef.current = apply
  })
  const urlQuery = useSearchParams()?.toString() ?? ""

  useEffect(() => {
    if (window.location.search.slice(1) === lastWritten.current) return
    applyRef.current(codec.fromParams(new URLSearchParams(window.location.search)))
    setRestored(true)
  }, [urlQuery, codec])

  const owned = codec.toParams(view).toString()
  useEffect(() => {
    if (!restored) return
    const next = new URLSearchParams(window.location.search)
    for (const key of codec.keys) next.delete(key)
    new URLSearchParams(owned).forEach((value, key) => next.set(key, value))
    const qs = next.toString()
    lastWritten.current = qs
    if (qs !== window.location.search.slice(1)) {
      window.history.replaceState(null, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`)
    }
  }, [restored, owned, codec])
}
