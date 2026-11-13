import type { RepoFlowMetricsResponse } from "@/lib/api/types"

/** Each repo costs a handful of GitHub requests, so the org-wide ranking covers only this many of the most
 * recently pushed repositories (and says so) rather than fanning out across a very large org. */
export const RANKING_REPO_CAP = 20

export interface FlowRankingRow {
  repo: string
  mergedPrs: number
  cycleHours: number | null
  reviewHours: number | null
  /** Failed share of the counted workflow runs across the repo, 0..1; null when it ran none. */
  failedRunRate: number | null
}

export type FlowRankingKey = "cycleHours" | "reviewHours" | "failedRunRate" | "mergedPrs"

/** One repo's ranking row from its flow-metrics response. The failed-run rate weights each workflow's
 * failure rate by its run count, so a busy workflow counts for more than an idle one. */
export function toRankingRow(repo: string, m: RepoFlowMetricsResponse): FlowRankingRow {
  const rated = m.workflows.filter((w) => w.failure_rate != null && w.runs > 0)
  const runs = rated.reduce((sum, w) => sum + w.runs, 0)
  const failed = rated.reduce((sum, w) => sum + w.runs * (w.failure_rate as number), 0)
  return {
    repo,
    mergedPrs: m.prs.merged_count,
    cycleHours: m.prs.median_cycle_hours,
    reviewHours: m.prs.median_first_review_hours,
    failedRunRate: runs > 0 ? failed / runs : null,
  }
}

/** Slowest / worst first by default; a repo with no data for the metric always goes last. */
export function sortRanking(rows: readonly FlowRankingRow[], key: FlowRankingKey, direction: "desc" | "asc" = "desc"): FlowRankingRow[] {
  const sign = direction === "desc" ? -1 : 1
  return [...rows].sort((a, b) => {
    const [x, y] = [a[key], b[key]]
    if (x == null && y == null) return a.repo.localeCompare(b.repo)
    if (x == null) return 1
    if (y == null) return -1
    return sign * (x - y) || a.repo.localeCompare(b.repo)
  })
}
