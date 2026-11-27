import type { ScanChanges } from "@/lib/api/types"

/** What to say next to a score card about how it moved since the previous scan. */
export type ScoreChange = { points: number } | { note: string }

/**
 * The change between the org's two latest scans, from `GET /orgs/{org}/analytics/changes`, for a card showing
 * `shown` (null/undefined when it has no score). None when there is nothing to compare with, or when the card does
 * not show the scan the comparison ends at (a result from another run, an older one). A pair whose scores are not on
 * the same basis (an errored check, a different scored set) is a note, never a number that would read as progress.
 */
export function scoreChangeFrom(changes: ScanChanges | undefined, shown: number | null | undefined): ScoreChange | null {
  if (!changes?.has_previous || changes.score === null || changes.previous_score === null) return null
  if (shown !== undefined && shown !== null && shown !== changes.score) return null
  if (!changes.comparable) return { note: "Not comparable with the previous scan" }
  return { points: changes.score - changes.previous_score }
}

/** "+3 vs last scan", "-5 vs last scan" or "No change vs last scan". */
export function scoreChangeText(change: { points: number }): string {
  if (change.points === 0) return "No change vs last scan"
  return `${change.points > 0 ? "+" : "-"}${Math.abs(change.points)} vs last scan`
}
