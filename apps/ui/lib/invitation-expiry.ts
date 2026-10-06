import { relativeTime } from "@/lib/format"
import type { InvitationOut } from "@/lib/api/types"

export type ExpiryTone = "none" | "normal" | "soon" | "expired"

export interface InvitationExpiry {
  label: string
  tone: ExpiryTone
}

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

function plural(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"}`
}

/** What to show in the "Expires" column. Only pending invitations still have a clock running:
 * accepted and revoked ones show a dash, and a pending one whose expiry has passed (the list can be
 * a little stale) reads as expired, same as the server's own "expired" status. */
export function invitationExpiry(
  inv: Pick<InvitationOut, "status" | "expires_at">,
  now: number = Date.now(),
): InvitationExpiry {
  if (inv.status === "accepted" || inv.status === "revoked") return { label: "—", tone: "none" }
  const expires = Date.parse(inv.expires_at)
  if (Number.isNaN(expires)) return { label: "—", tone: "none" }

  const left = expires - now
  if (inv.status === "expired" || left <= 0) {
    return { label: `expired ${relativeTime(inv.expires_at)}`, tone: "expired" }
  }
  if (left < HOUR) return { label: `in ${plural(Math.max(1, Math.ceil(left / MINUTE)), "minute")}`, tone: "soon" }
  if (left < DAY) return { label: `in ${plural(Math.floor(left / HOUR), "hour")}`, tone: "soon" }
  return { label: `in ${plural(Math.floor(left / DAY), "day")}`, tone: "normal" }
}
