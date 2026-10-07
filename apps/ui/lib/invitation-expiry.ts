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

/** The status to show: a pending invitation whose expiry has passed is already expired, even if the
 * (cached) list hasn't caught up with the server's lazy pending -> expired flip. */
export function effectiveInvitationStatus(
  inv: Pick<InvitationOut, "status" | "expires_at">,
  now: number = Date.now(),
): InvitationOut["status"] {
  if (inv.status !== "pending") return inv.status
  const expires = Date.parse(inv.expires_at)
  return !Number.isNaN(expires) && expires <= now ? "expired" : "pending"
}

/** What to show in the "Expires" column. Only pending invitations still have a clock running:
 * accepted and revoked ones show a dash. Remaining time rounds *up* ("in 7 days" for a fresh
 * 7-day invitation, never "in 6 days"), except that the minute count stops at 59. */
export function invitationExpiry(
  inv: Pick<InvitationOut, "status" | "expires_at">,
  now: number = Date.now(),
): InvitationExpiry {
  if (inv.status === "accepted" || inv.status === "revoked") return { label: "—", tone: "none" }
  const expires = Date.parse(inv.expires_at)
  if (Number.isNaN(expires)) return { label: "—", tone: "none" }

  if (effectiveInvitationStatus(inv, now) === "expired") {
    return { label: `expired ${relativeTime(inv.expires_at)}`, tone: "expired" }
  }
  const left = expires - now
  if (left < HOUR) return { label: `in ${plural(Math.min(59, Math.ceil(left / MINUTE)), "minute")}`, tone: "soon" }
  if (left < DAY) return { label: `in ${plural(Math.ceil(left / HOUR), "hour")}`, tone: "soon" }
  return { label: `in ${plural(Math.ceil(left / DAY), "day")}`, tone: "normal" }
}
