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
      return `${value.numerator}/${value.denominator}`
    default:
      return ""
  }
}

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
      // The summary can match while the affected repos moved (2/5 on repo-a vs repo-b): compare the values too.
      if (a !== b) diff.changed.push({ check: now, before: a, after: b })
      else if (JSON.stringify(was.value) !== JSON.stringify(now.value)) {
        diff.changed.push({ check: now, before: `${a} (affected repos changed)`.trim(), after: a })
      }
    }
  }
  return diff
}
