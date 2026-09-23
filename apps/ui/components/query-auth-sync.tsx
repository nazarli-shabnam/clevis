"use client"

import { useRef } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useAuth } from "@/lib/auth-context"

/**
 * Drops the entire React Query cache whenever the signed-in user changes (sign-out or
 * account switch), so user B can't be served user A's cached PAT or data keyed on `org`.
 *
 * Clears during render, not in an effect: it renders just before `<AuthGuard>`, so the
 * clear lands before any authenticated `useQuery` reads the cache on the same commit.
 */
export function QueryAuthSync() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const lastUserId = useRef<number | null | undefined>(undefined)

  const currentUserId = user?.id ?? null
  if (lastUserId.current === undefined) {
    // First observation on mount — nothing cached under a prior identity yet.
    lastUserId.current = currentUserId
  } else if (lastUserId.current !== currentUserId) {
    lastUserId.current = currentUserId
    queryClient.clear()
  }

  return null
}
