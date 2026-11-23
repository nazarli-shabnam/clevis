import type { CockpitResponse } from "@/lib/api/types"
import { formatBytes } from "@/lib/format"

/** One comparable number pair. `a` is the earlier period / first account, `b` the later period / second account. */
export interface CompareMetric {
  label: string
  a: number | null
  b: number | null
  /** true: a rise is good; false: a rise is bad; null: neutral (no colouring). */
  higherIsBetter: boolean | null
  format: (n: number) => string
}

export type Tone = "better" | "worse" | "same"

export interface Change {
  text: string
  tone: Tone | null
}

const count = (n: number) => String(Math.round(n * 10) / 10)
const sum = (xs: number[]) => xs.reduce((total, x) => total + x, 0)
const mean = (xs: number[]) => (xs.length ? sum(xs) / xs.length : null)
const halves = <T,>(xs: T[]): [T[], T[]] => [xs.slice(0, xs.length / 2), xs.slice(xs.length / 2)]

/** Previous vs last half of each 4-week series on the cockpit ("last 2 weeks" against "the 2 before"). */
export function periodMetrics(c: CockpitResponse): CompareMetric[] {
  const [commitsPrev, commitsLast] = halves(c.commit_activity_4w)
  const [bucketsPrev, bucketsLast] = halves(c.pr_merge_rate_4w)
  const [releasesPrev, releasesLast] = halves(c.release_cadence_4w)
  // Weeks with no merged PRs average 0 days; they are "no data", not a fast week.
  const [cyclePrev, cycleLast] = halves(c.pr_cycle_time_8w.map((w) => w.avg_days).filter((d) => d > 0))
  const scores = c.score_trend
  const has = (xs: unknown[]) => xs.length > 0
  return [
    {
      label: "Commits",
      a: has(commitsPrev) ? sum(commitsPrev) : null,
      b: has(commitsLast) ? sum(commitsLast) : null,
      higherIsBetter: null,
      format: count,
    },
    {
      label: "Pull requests opened",
      a: has(bucketsPrev) ? sum(bucketsPrev.map((x) => x.opened)) : null,
      b: has(bucketsLast) ? sum(bucketsLast.map((x) => x.opened)) : null,
      higherIsBetter: null,
      format: count,
    },
    {
      label: "Pull requests merged",
      a: has(bucketsPrev) ? sum(bucketsPrev.map((x) => x.merged)) : null,
      b: has(bucketsLast) ? sum(bucketsLast.map((x) => x.merged)) : null,
      higherIsBetter: true,
      format: count,
    },
    {
      label: "Releases",
      a: has(releasesPrev) ? sum(releasesPrev) : null,
      b: has(releasesLast) ? sum(releasesLast) : null,
      higherIsBetter: null,
      format: count,
    },
    // Cycle time is read from 8 weeks, so these are the older and newer halves of the weeks that had merges.
    { label: "Avg PR cycle time (days)", a: mean(cyclePrev), b: mean(cycleLast), higherIsBetter: false, format: count },
    {
      label: "Security score (previous scan vs latest)",
      a: scores.length >= 2 ? scores[scores.length - 2] : null,
      b: scores.length >= 2 ? scores[scores.length - 1] : null,
      higherIsBetter: true,
      format: count,
    },
  ]
}

/** The same cockpit numbers for two accounts, `a` first. */
export function orgMetrics(a: CockpitResponse, b: CockpitResponse): CompareMetric[] {
  const total = (c: CockpitResponse) => sum(c.commit_activity_4w)
  const merged = (c: CockpitResponse) => sum(c.pr_merge_rate_4w.map((x) => x.merged))
  return [
    { label: "Security score", a: a.latest_score, b: b.latest_score, higherIsBetter: true, format: count },
    { label: "Repositories", a: a.repo_count, b: b.repo_count, higherIsBetter: null, format: count },
    { label: "Members", a: a.member_count, b: b.member_count, higherIsBetter: null, format: count },
    { label: "Open pull requests", a: a.open_pr_count, b: b.open_pr_count, higherIsBetter: false, format: count },
    { label: "Commits (4w)", a: total(a), b: total(b), higherIsBetter: null, format: count },
    { label: "Pull requests merged (4w)", a: merged(a), b: merged(b), higherIsBetter: true, format: count },
    {
      label: "Actions cache in use",
      a: a.total_cache_size_bytes,
      b: b.total_cache_size_bytes,
      higherIsBetter: false,
      format: formatBytes,
    },
  ]
}

/** "+3", "-2.5" or "no change" from a to b, with whether that is good, bad or neutral; null when either side is missing. */
export function changeBetween(m: CompareMetric): Change | null {
  if (m.a === null || m.b === null) return null
  const diff = Math.round((m.b - m.a) * 10) / 10
  if (diff === 0) return { text: "no change", tone: m.higherIsBetter === null ? null : "same" }
  const text = `${diff > 0 ? "+" : "-"}${m.format(Math.abs(diff))}`
  if (m.higherIsBetter === null) return { text, tone: null }
  return { text, tone: diff > 0 === m.higherIsBetter ? "better" : "worse" }
}
