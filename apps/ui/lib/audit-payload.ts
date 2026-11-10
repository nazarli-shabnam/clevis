/** An audit row's `payload` (a JSON string) as display pairs. Empty for `{}` or nothing stored, so a row
 * with no detail isn't offered for expansion. Non-object JSON and unparseable text are shown whole. */
export function payloadEntries(payload: string): [string, string][] {
  const raw = payload.trim()
  if (!raw) return []
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return [["payload", raw]]
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return [["payload", typeof parsed === "string" ? parsed : JSON.stringify(parsed)]]
  }
  return Object.entries(parsed).map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value)])
}

/** One-line form for the table cell, e.g. `check_id=branch_protection, dry_run=false`. */
export function payloadSummary(payload: string): string {
  return payloadEntries(payload)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ")
}

/** Pretty-printed JSON for the copy button (the raw text if it isn't JSON). */
export function prettyPayload(payload: string): string {
  try {
    return JSON.stringify(JSON.parse(payload), null, 2)
  } catch {
    return payload
  }
}
