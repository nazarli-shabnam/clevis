import { describe, expect, it } from "vitest"
import { changeBetween, orgMetrics, periodMetrics } from "@/lib/compare"
import type { CockpitResponse } from "@/lib/api/types"

const cockpit = (over: Partial<CockpitResponse> = {}): CockpitResponse =>
  ({
    repo_count: 10,
    member_count: 5,
    latest_score: 80,
    score_trend: [70, 80],
    open_pr_count: 4,
    pr_merge_rate_4w: [
      { week: "w1", opened: 1, merged: 1 },
      { week: "w2", opened: 2, merged: 1 },
      { week: "w3", opened: 5, merged: 4 },
      { week: "w4", opened: 3, merged: 2 },
    ],
    commit_activity_4w: [1, 2, 10, 20],
    total_cache_size_bytes: 1_000_000,
    release_cadence_4w: [0, 1, 1, 1],
    pr_cycle_time_8w: [2, 4, 0, 0, 1, 1, 0, 3].map((avg_days, i) => ({ week: `w${i}`, avg_days })),
    ...over,
  }) as CockpitResponse

const byLabel = (rows: ReturnType<typeof periodMetrics>) => Object.fromEntries(rows.map((r) => [r.label, r]))

describe("periodMetrics", () => {
  it("compares the last two weeks with the two before, per series", () => {
    const m = byLabel(periodMetrics(cockpit()))
    expect([m["Commits"].a, m["Commits"].b]).toEqual([3, 30])
    expect([m["Pull requests opened"].a, m["Pull requests opened"].b]).toEqual([3, 8])
    expect([m["Pull requests merged"].a, m["Pull requests merged"].b]).toEqual([2, 6])
    expect([m["Releases"].a, m["Releases"].b]).toEqual([1, 2])
    // weeks without merges (0 days) are left out; the remaining 2,4 | 1,1,3 average per half
    expect(m["Avg PR cycle time (days)"].a).toBe(3)
    expect(m["Avg PR cycle time (days)"].b).toBeCloseTo(5 / 3)
    expect([m["Security score (previous scan vs latest)"].a, m["Security score (previous scan vs latest)"].b]).toEqual([70, 80])
  })

  it("has no value rather than a made-up one when there is no history", () => {
    const m = byLabel(
      periodMetrics(cockpit({ score_trend: [90], commit_activity_4w: [], pr_cycle_time_8w: [] })),
    )
    expect(m["Security score (previous scan vs latest)"].a).toBeNull()
    expect(m["Commits"].a).toBeNull()
    expect(m["Avg PR cycle time (days)"].b).toBeNull()
  })
})

describe("orgMetrics and changeBetween", () => {
  it("lines two accounts up and judges the change by the metric's direction", () => {
    const rows = byLabel(orgMetrics(cockpit(), cockpit({ latest_score: 90, open_pr_count: 9, repo_count: 12 })))
    expect(changeBetween(rows["Security score"])).toEqual({ text: "+10", tone: "better" })
    expect(changeBetween(rows["Open pull requests"])).toEqual({ text: "+5", tone: "worse" })
    expect(changeBetween(rows["Repositories"])).toEqual({ text: "+2", tone: null })
    expect(changeBetween(rows["Members"])).toEqual({ text: "no change", tone: null })
    expect(changeBetween(rows["Actions cache in use"])).toEqual({ text: "no change", tone: "same" })
  })

  it("shows no change when either side is unknown", () => {
    const rows = byLabel(orgMetrics(cockpit({ latest_score: null }), cockpit()))
    expect(changeBetween(rows["Security score"])).toBeNull()
  })
})
