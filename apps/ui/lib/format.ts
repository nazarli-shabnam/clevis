/**
 * Pure formatting utilities — no React deps, no side effects.
 * Used across cache page, activity list, check cards, and job queue.
 */

/** "just now" | "3 minutes ago" | "2 days ago" | "1 month ago" */
export function relativeTime(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  if (isNaN(then)) return "unknown"
  const diffMs = now - then
  // Future timestamps — treat as "just now" rather than crashing
  if (diffMs < 0) return "just now"
  const sec = Math.floor(diffMs / 1000)
  if (sec < 60)    return "just now"
  const min = Math.floor(sec / 60)
  if (min < 60)    return `${min} minute${min === 1 ? "" : "s"} ago`
  const hr = Math.floor(min / 60)
  if (hr < 24)     return `${hr} hour${hr === 1 ? "" : "s"} ago`
  const day = Math.floor(hr / 24)
  if (day < 30)    return `${day} day${day === 1 ? "" : "s"} ago`
  const month = Math.floor(day / 30)
  if (month < 12)  return `${month} month${month === 1 ? "" : "s"} ago`
  const year = Math.floor(month / 12)
  return `${year} year${year === 1 ? "" : "s"} ago`
}

/** "May 24, 2026 at 14:33 UTC" (24-hour clock, UTC) */
export function exactTime(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
    timeZoneName: "short",
  })
}

/** "512 B" | "1.4 KB" | "2.3 MB" | "1.1 GB" */
export function formatBytes(n: number): string {
  if (n === 0)              return "0 B"
  if (n < 1024)             return `${n} B`
  if (n < 1024 * 1024)      return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3)        return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(1)} GB`
}

/**
 * "github.clear_actions_cache" → "Clear Actions Cache"
 * Falls back to title-casing the last dot-segment.
 */
export function jobTypeLabel(slug: string): string {
  const map: Record<string, string> = {
    "github.clear_actions_cache": "Clear Actions Cache",
  }
  if (map[slug]) return map[slug]
  const last = slug.split(".").slice(-1)[0] ?? slug
  return last
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export type Staleness = "fresh" | "stale" | "old"

/**
 * Classify how stale a last-accessed timestamp is.
 * < 7 days  → "fresh"
 * 7–30 days → "stale"
 * > 30 days → "old"
 */
export function classifyStaleness(iso: string): Staleness {
  const diffMs = Date.now() - new Date(iso).getTime()
  if (isNaN(diffMs)) return "old"
  const days = diffMs / (1000 * 60 * 60 * 24)
  if (days < 7)  return "fresh"
  if (days < 30) return "stale"
  return "old"
}

export const stalenessColor: Record<Staleness, { text: string; dot: string }> = {
  fresh: { text: "text-green-400",  dot: "bg-green-400"  },
  stale: { text: "text-yellow-400", dot: "bg-yellow-400" },
  old:   { text: "text-red-400",    dot: "bg-red-400"    },
}
