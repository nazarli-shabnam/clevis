"use client"

import { useEffect, useState } from "react"
import { api } from "@/lib/api/client"
import type { MyInvitation } from "@/lib/api/types"

/**
 * The signed-in user's pending invitations, read from the server so they survive a reload and a new tab
 * (the login response only carries a token-less summary in memory). `refetchKey` re-reads, e.g. on navigation,
 * so an accepted invitation drops out. `ownerId` ties the list to one account: signing in as someone else swaps the
 * user without an unauthenticated render in between, and the previous account's list (with its accept links) must
 * not show for even one frame. Failures read as "none": this only ever adds a convenience banner.
 */
export function useMyInvitations(enabled: boolean, refetchKey?: string, ownerId?: number): MyInvitation[] {
  const [loaded, setLoaded] = useState<{ ownerId: number | undefined; rows: MyInvitation[] }>({ ownerId, rows: [] })
  useEffect(() => {
    if (!enabled) {
      setLoaded({ ownerId, rows: [] })
      return
    }
    let cancelled = false
    api.invitations
      .mine()
      .then((rows) => !cancelled && setLoaded({ ownerId, rows }))
      .catch(() => !cancelled && setLoaded({ ownerId, rows: [] }))
    return () => {
      cancelled = true
    }
  }, [enabled, refetchKey, ownerId])
  return loaded.ownerId === ownerId ? loaded.rows : []
}
