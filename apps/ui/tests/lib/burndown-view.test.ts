import { describe, expect, it } from "vitest"

import {
  DEFAULT_BURNDOWN_VIEW,
  burndownViewFromParams,
  burndownViewToParams,
  visibleBurndownRepos,
} from "@/lib/burndown-view"

const repo = (name: string, open: Record<string, number>, breaches: number, oldest: number) => ({
  repo: name,
  open,
  breaches,
  oldest_age_days: oldest,
  oldest_alert_number: 1,
})
const REPOS = [
  repo("acme/api", { critical: 2, high: 0 }, 1, 10),
  repo("acme/web", { critical: 0, high: 3 }, 3, 40),
  repo("acme/cli", { critical: 1, high: 1 }, 0, 25),
]

describe("burndown view <-> URL", () => {
  it("keeps defaults out of the query string and round-trips the rest", () => {
    expect(burndownViewToParams(DEFAULT_BURNDOWN_VIEW).toString()).toBe("")
    const view = { days: 90, severity: "critical" as const, sort: "breaches" as const, repo: "acme/api" }
    expect(burndownViewFromParams(new URLSearchParams(burndownViewToParams(view).toString()))).toEqual(view)
  })

  it("falls back to defaults for unsupported values", () => {
    expect(burndownViewFromParams(new URLSearchParams("days=45&severity=low&sort=x"))).toEqual(DEFAULT_BURNDOWN_VIEW)
  })
})

describe("visibleBurndownRepos", () => {
  it("sorts by oldest alert by default", () => {
    expect(visibleBurndownRepos(REPOS, DEFAULT_BURNDOWN_VIEW).map((r) => r.repo)).toEqual(["acme/web", "acme/cli", "acme/api"])
  })

  it("sorts by breaches when asked", () => {
    const view = { ...DEFAULT_BURNDOWN_VIEW, sort: "breaches" as const }
    expect(visibleBurndownRepos(REPOS, view).map((r) => r.repo)).toEqual(["acme/web", "acme/api", "acme/cli"])
  })

  it("filters by severity and by the deep-linked repo", () => {
    expect(visibleBurndownRepos(REPOS, { ...DEFAULT_BURNDOWN_VIEW, severity: "critical" }).map((r) => r.repo)).toEqual([
      "acme/cli",
      "acme/api",
    ])
    expect(visibleBurndownRepos(REPOS, { ...DEFAULT_BURNDOWN_VIEW, repo: "acme/web" }).map((r) => r.repo)).toEqual(["acme/web"])
  })

  it("breaks equal ages by repository name", () => {
    const tied = [repo("acme/b", { high: 1 }, 0, 5), repo("acme/a", { high: 1 }, 0, 5)]
    expect(visibleBurndownRepos(tied, DEFAULT_BURNDOWN_VIEW).map((r) => r.repo)).toEqual(["acme/a", "acme/b"])
  })

  it("breaks equal breach counts by oldest alert", () => {
    const tied = [repo("acme/b", { high: 1 }, 2, 5), repo("acme/a", { high: 1 }, 2, 9)]
    const view = { ...DEFAULT_BURNDOWN_VIEW, sort: "breaches" as const }
    expect(visibleBurndownRepos(tied, view).map((r) => r.repo)).toEqual(["acme/a", "acme/b"])
  })

  it("treats a missing severity count as none", () => {
    expect(visibleBurndownRepos([repo("acme/a", {}, 0, 1)], { ...DEFAULT_BURNDOWN_VIEW, severity: "high" })).toEqual([])
  })
})
