import type { DependabotBurndown } from "@/lib/api/types"

export const BURNDOWN_WINDOWS = [30, 60, 90] as const
export type BurndownSeverity = "all" | "critical" | "high"
export type BurndownSort = "age" | "breaches"

/** The burn-down page's controls, kept in the query string so a retro view can be shared. `repo` is the
 * deep link a notification uses: it narrows the list to one "owner/name" repository. */
export interface BurndownView {
  days: number
  severity: BurndownSeverity
  sort: BurndownSort
  repo: string
}

export const DEFAULT_BURNDOWN_VIEW: BurndownView = { days: 30, severity: "all", sort: "age", repo: "" }

export function burndownViewToParams(view: BurndownView): URLSearchParams {
  const params = new URLSearchParams()
  if (view.days !== DEFAULT_BURNDOWN_VIEW.days) params.set("days", String(view.days))
  if (view.severity !== "all") params.set("severity", view.severity)
  if (view.sort !== DEFAULT_BURNDOWN_VIEW.sort) params.set("sort", view.sort)
  if (view.repo) params.set("repo", view.repo)
  return params
}

export function burndownViewFromParams(params: URLSearchParams): BurndownView {
  const days = Number(params.get("days"))
  const severity = params.get("severity")
  const sort = params.get("sort")
  return {
    days: (BURNDOWN_WINDOWS as readonly number[]).includes(days) ? days : DEFAULT_BURNDOWN_VIEW.days,
    severity: severity === "critical" || severity === "high" ? severity : "all",
    sort: sort === "breaches" ? "breaches" : "age",
    repo: params.get("repo") ?? "",
  }
}

type RepoRow = DependabotBurndown["repos"][number]

/** Applies the repo and severity filters, then orders by oldest alert (default) or most SLA breaches. */
export function visibleBurndownRepos(repos: readonly RepoRow[], view: BurndownView): RepoRow[] {
  return repos
    .filter((r) => !view.repo || r.repo === view.repo)
    .filter((r) => view.severity === "all" || (r.open[view.severity] ?? 0) > 0)
    .sort((a, b) =>
      view.sort === "breaches"
        ? b.breaches - a.breaches || b.oldest_age_days - a.oldest_age_days
        : b.oldest_age_days - a.oldest_age_days || a.repo.localeCompare(b.repo),
    )
}

/** The Clevis repo page, opened on its Security tab, for an "owner/name" repo; null for any other shape. */
export function burndownRepoHref(fullName: string): string | null {
  const [owner, name, ...rest] = fullName.split("/")
  if (!owner || !name || rest.length > 0) return null
  return `/repos/${encodeURIComponent(owner)}~${encodeURIComponent(name)}?tab=security`
}
