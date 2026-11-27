import type { CheckResult } from "@/lib/api/types"

/** Per-check rows of a stored scan, as the history export returns them (permissive: older runners stored
 * slightly different shapes). */
export type StoredCheck = Partial<CheckResult>

const FAILING = new Set(["fail", "error"])

export const isFailing = (c: StoredCheck) => !!c.status && FAILING.has(c.status)

/** A short, comparable text for a check's value, e.g. "2 critical, 1 high" or "3/5". "" when there is none. */
export function checkValueSummary(value: CheckResult["value"] | undefined): string {
  if (!value) return ""
  switch (value.type) {
    case "text":
      return value.text
    case "boolean":
      return value.enabled ? "Enabled" : "Disabled"
    case "severity_counts": {
      const parts = (["critical", "high", "medium", "low"] as const)
        .filter((k) => value[k] > 0)
        .map((k) => `${value[k]} ${k}`)
      return parts.length > 0 ? parts.join(", ") : "No open alerts"
    }
    case "ratio":
      return `${value.numerator}/${value.denominator}${value.unknown ? ` (${value.unknown} unknown)` : ""}`
    default:
      return ""
  }
}

const reposOf = (value: CheckResult["value"] | undefined) => (value?.type === "ratio" || value?.type === "severity_counts" ? (value.repos ?? []).join(",") : "")

export interface ScanCounts {
  failed: number
  errored: number
  waived: number
}

export function scanCounts(checks: readonly StoredCheck[]): ScanCounts {
  return {
    failed: checks.filter((c) => c.status === "fail").length,
    errored: checks.filter((c) => c.status === "error").length,
    waived: checks.filter((c) => !!c.waived).length,
  }
}

export interface ScanDiff {
  /** Failing in the newer scan but not in the older one (including checks the older scan did not have). */
  newlyFailing: StoredCheck[]
  /** Failing in the older scan, present and no longer failing in the newer one. */
  fixed: StoredCheck[]
  /** Failing in both, with a different value (e.g. more repositories affected). */
  changed: { check: StoredCheck; before: string; after: string }[]
}

/** What moved between two stored scans, in either direction of time: `older` and `newer` are just the
 * "from" and "to" side. */
export function diffScans(older: readonly StoredCheck[], newer: readonly StoredCheck[]): ScanDiff {
  const before = new Map(older.flatMap((c) => (c.id ? [[c.id, c] as const] : [])))
  const after = new Map(newer.flatMap((c) => (c.id ? [[c.id, c] as const] : [])))
  const diff: ScanDiff = { newlyFailing: [], fixed: [], changed: [] }
  for (const [id, now] of after) {
    const was = before.get(id)
    if (isFailing(now) && (!was || !isFailing(was))) diff.newlyFailing.push(now)
    else if (!isFailing(now) && was && isFailing(was)) diff.fixed.push(now)
    else if (isFailing(now) && was && isFailing(was)) {
      const [a, b] = [checkValueSummary(was.value), checkValueSummary(now.value)]
      // The summary can match while the affected repos moved (2/5 on repo-a vs repo-b): compare them too.
      if (a !== b) diff.changed.push({ check: now, before: a, after: b })
      else if (reposOf(was.value) !== reposOf(now.value)) {
        diff.changed.push({ check: now, before: `${a} (affected repos changed)`.trim(), after: a })
      }
    }
  }
  return diff
}

/** The scan stores at most this many names per check (packages/checks MAX_AFFECTED_REPOS): a full list means
 * the repo may simply not have fit. */
const AFFECTED_REPOS_CAP = 25

export type RepoCheckState = "fail" | "pass" | "unknown"

/**
 * How one scan saw one repository for one check, from the repos the check named when it failed. `null` when the
 * check is not per-repository (org-wide MFA, text values), so it has no place on a repo's timeline.
 * "unknown" when the scan cannot say: the check errored or did not apply, or it failed without naming repos
 * (older scans) or with a list that hit the storage cap and does not include this repo.
 */
export function repoCheckState(check: StoredCheck, repo: string): RepoCheckState | null {
  const value = check.value
  if (value?.type !== "ratio" && value?.type !== "severity_counts") return null
  if (check.status === "error" || check.status === "not_applicable" || !check.status) return "unknown"
  const named = (value.repos ?? []).map((r) => r.toLowerCase())
  if (check.status === "fail" && named.includes(repo.toLowerCase())) return "fail"
  if (check.status === "pass") return "pass"
  return named.length > 0 && named.length < AFFECTED_REPOS_CAP ? "pass" : "unknown"
}

export interface RepoHistoryPoint {
  scanId: number
  at: string
  state: RepoCheckState
}

export interface RepoCheckHistory {
  id: string
  title: string
  /** Oldest first. */
  points: RepoHistoryPoint[]
  /** The newest scan's state for this repo, and when that state began (the first scan of the current run). */
  current: RepoCheckState
  since: string
  /** True when the run reaches back to the oldest scan in the window, so it may have begun earlier. */
  sinceAtLeast: boolean
}

interface HistoryScan {
  id: number
  created_at: string
  checks: readonly StoredCheck[]
}

/** One timeline per per-repository check that ever showed this repo as failing, built from stored scans. Checks
 * the repo always passed are left out: the timeline exists to show when something started and which scan did it. */
export function repoCheckHistory(scans: readonly HistoryScan[], repo: string): RepoCheckHistory[] {
  const ordered = [...scans].sort((a, b) => a.created_at.localeCompare(b.created_at))
  const byCheck = new Map<string, { title: string; points: RepoHistoryPoint[] }>()
  for (const scan of ordered) {
    for (const check of scan.checks) {
      if (!check.id) continue
      const state = repoCheckState(check, repo)
      if (state === null) continue
      const entry = byCheck.get(check.id) ?? { title: check.title ?? check.id, points: [] }
      entry.points.push({ scanId: scan.id, at: scan.created_at, state })
      byCheck.set(check.id, entry)
    }
  }
  const out: RepoCheckHistory[] = []
  for (const [id, { title, points }] of byCheck) {
    if (!points.some((p) => p.state === "fail")) continue
    const current = points[points.length - 1].state
    let start = points.length - 1
    while (start > 0 && points[start - 1].state === current) start--
    out.push({ id, title, points, current, since: points[start].at, sinceAtLeast: start === 0 })
  }
  return out.sort((a, b) => Number(b.current === "fail") - Number(a.current === "fail") || a.title.localeCompare(b.title))
}
