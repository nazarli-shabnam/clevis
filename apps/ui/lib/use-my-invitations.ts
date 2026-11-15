"use client"

import { useEffect, useState } from "react"
import { api } from "@/lib/api/client"
import type { MyInvitation } from "@/lib/api/types"

/**
 * The signed-in user's pending invitations, read from the server so they survive a reload and a new tab
 * (the login response only carries a token-less summary in memory). `refetchKey` re-reads, e.g. on navigation,
 * so an accepted invitation drops out. Failures read as "none": this only ever adds a convenience banner.
 */
export function useMyInvitations(enabled: boolean, refetchKey?: string): MyInvitation[] {
  const [invitations, setInvitations] = useState<MyInvitation[]>([])
  useEffect(() => {
    if (!enabled) {
      setInvitations([])
      return
    }
    let cancelled = false
    api.invitations
      .mine()
      .then((rows) => !cancelled && setInvitations(rows))
      .catch(() => !cancelled && setInvitations([]))
    return () => {
      cancelled = true
    }
  }, [enabled, refetchKey])
  return invitations
}
