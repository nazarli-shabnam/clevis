import { describe, expect, it } from "vitest"

import { sortRanking, toRankingRow, type FlowRankingRow } from "@/lib/flow-ranking"
import type { RepoFlowMetricsResponse } from "@/lib/api/types"

const metrics = (
  over: Partial<RepoFlowMetricsResponse["prs"]>,
  workflows: RepoFlowMetricsResponse["workflows"],
): RepoFlowMetricsResponse => ({
  repository: "acme/x",
  window_days: 30,
  prs: { merged_count: 4, median_cycle_hours: 10, median_first_review_hours: 2, review_sample_size: 4, merged_without_review: 0, ...over },
  workflows,
  workflows_truncated: false,
})
const wf = (runs: number, failure_rate: number | null) => ({ name: "CI", runs, failure_rate, avg_duration_seconds: 60, flaky_commits: 0 })

describe("toRankingRow", () => {
  it("weights each workflow's failure rate by its runs", () => {
    const row = toRankingRow("api", metrics({}, [wf(10, 0.5), wf(30, 0)]))
    expect(row).toEqual({ repo: "api", mergedPrs: 4, cycleHours: 10, reviewHours: 2, failedRunRate: 5 / 40 })
  })

  it("has no failed-run rate when no workflow ran", () => {
    expect(toRankingRow("api", metrics({}, [wf(0, null), wf(5, null)])).failedRunRate).toBeNull()
  })
})

describe("sortRanking", () => {
  const row = (repo: string, cycleHours: number | null): FlowRankingRow => ({
    repo,
    mergedPrs: 0,
    cycleHours,
    reviewHours: null,
    failedRunRate: null,
  })
  const rows = [row("a", 5), row("b", null), row("c", 20), row("d", 12)]

  it("puts the slowest first and repos without data last", () => {
    expect(sortRanking(rows, "cycleHours").map((r) => r.repo)).toEqual(["c", "d", "a", "b"])
  })

  it("reverses to fastest first but still keeps repos without data last", () => {
    expect(sortRanking(rows, "cycleHours", "asc").map((r) => r.repo)).toEqual(["a", "d", "c", "b"])
  })
})
