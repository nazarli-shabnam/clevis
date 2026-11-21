export type ExpiryFilter = "any" | "expired" | "7" | "14" | "30"

export const EXPIRY_FILTER_LABELS: Record<ExpiryFilter, string> = {
  any: "Any expiry",
  expired: "Expired",
  "7": "Expiring in 7 days",
  "14": "Expiring in 14 days",
  "30": "Expiring in 30 days",
}

/** Whether an expiry (ISO time, or null for "never") falls in the chosen window. "Expiring in N" means still
 * valid now but ending within N days; never-expiring rows match only "any". */
export function matchesExpiry(expiresAt: string | null | undefined, filter: ExpiryFilter, now = Date.now()): boolean {
  if (filter === "any") return true
  const at = expiresAt ? Date.parse(expiresAt) : NaN
  if (Number.isNaN(at)) return false
  if (filter === "expired") return at <= now
  return at > now && at <= now + Number(filter) * 86_400_000
}
