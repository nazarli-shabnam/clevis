import type { RepoSummary } from "@/lib/api/types"

export type VisibilityFilter = "all" | "public" | "private"
export type StatusFilter = "all" | "active" | "archived"

export interface RepoFilters {
  /** "" = any language */
  language: string
  visibility: VisibilityFilter
  status: StatusFilter
  /** Only repos with no push in the last STALE_DAYS days (or never pushed). */
  stale: boolean
}

export const NO_REPO_FILTERS: RepoFilters = { language: "", visibility: "all", status: "all", stale: false }

export const STALE_DAYS = 90

const DAY_MS = 24 * 60 * 60 * 1000

export function hasActiveRepoFilters(filters: RepoFilters): boolean {
  return (
    filters.language !== "" || filters.visibility !== "all" || filters.status !== "all" || filters.stale
  )
}

/** Distinct, alphabetised languages present in `repos` (repos without a language are skipped). */
export function repoLanguages(repos: readonly RepoSummary[]): string[] {
  return [...new Set(repos.flatMap((r) => (r.language ? [r.language] : [])))].sort((a, b) => a.localeCompare(b))
}

function isStale(repo: RepoSummary, now: number): boolean {
  if (!repo.pushed_at) return true // never pushed
  const pushed = Date.parse(repo.pushed_at)
  if (Number.isNaN(pushed)) return false // unparsable: don't claim it's stale
  return now - pushed > STALE_DAYS * DAY_MS
}

/** Applies every active filter; all conditions must hold. */
export function filterRepos(repos: readonly RepoSummary[], filters: RepoFilters, now: number = Date.now()): RepoSummary[] {
  return repos.filter((r) => {
    if (filters.language && r.language !== filters.language) return false
    if (filters.visibility === "private" && !r.private) return false
    if (filters.visibility === "public" && r.private) return false
    // `archived` is absent from older API responses, which then read as "not archived".
    if (filters.status === "archived" && !r.archived) return false
    if (filters.status === "active" && r.archived) return false
    if (filters.stale && !isStale(r, now)) return false
    return true
  })
}
