import type { PullSummary } from "@/lib/api/types"

export type PullSort = "newest" | "oldest" | "updated"

export const PULL_SORT_LABELS: Record<PullSort, string> = {
  newest: "Newest first",
  oldest: "Oldest first",
  updated: "Recently updated",
}

/** Falls back to the opened time, so a PR without `updated_at` sorts as untouched since it was opened. */
const touched = (p: PullSummary) => p.updated_at ?? p.created_at

/** A new array ordered per `sort`. */
export function sortPulls<T extends PullSummary>(pulls: T[], sort: PullSort): T[] {
  const by =
    sort === "oldest"
      ? (a: T, b: T) => a.created_at.localeCompare(b.created_at)
      : sort === "updated"
        ? (a: T, b: T) => touched(b).localeCompare(touched(a))
        : (a: T, b: T) => b.created_at.localeCompare(a.created_at)
  return [...pulls].sort(by)
}

/** The server's definition of stale (services/pr_nudge.py `_is_stale`): not a draft, opened and last
 * touched more than `staleDays` ago, so the badge flags the PRs "Nudge stale PRs" would act on. */
export function isStale(p: PullSummary, staleDays: number, now = Date.now()): boolean {
  if (p.draft) return false
  const cutoff = now - staleDays * 86_400_000
  return new Date(p.created_at).getTime() <= cutoff && new Date(touched(p)).getTime() <= cutoff
}

/** Whole days since `iso`. */
export function ageDays(iso: string, now = Date.now()): number {
  return Math.max(0, Math.floor((now - new Date(iso).getTime()) / 86_400_000))
}
