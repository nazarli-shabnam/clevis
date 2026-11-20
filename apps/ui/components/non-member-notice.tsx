"use client"

import Link from "next/link"
import { useNonMemberOrg } from "@/lib/use-scope-membership"

/** Explains an org scope held only through a saved token: without it, the org pages 403 and nothing says why. */
export function NonMemberNotice() {
  const org = useNonMemberOrg()
  if (!org) return null
  return (
    <p role="status" className="card mb-4 px-4 py-3 text-sm text-muted-foreground">
      You are not a member of {org} in Clevis, so only Overview and Health &amp; Security work here (through its saved
      token). To use the other pages, get invited or reconnect the organization, or{" "}
      <Link href="/settings" className="text-primary underline-offset-2 hover:underline">
        remove the saved token
      </Link>
      .
    </p>
  )
}
