"use client"

import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { relativeTime } from "@/lib/format"
import type { MyViewResponse, MyViewRunSummary } from "@/lib/api/types"

const FAILED_CONCLUSIONS = new Set(["failure", "timed_out", "startup_failure"])
const MAX_ROWS = 5

/**
 * The latest run per repository+workflow, kept only if that latest run failed -- so a workflow
 * that failed and was then re-run green no longer shows up as a problem.
 */
export function latestFailingRuns(runs: MyViewRunSummary[]): MyViewRunSummary[] {
  const latest = new Map<string, MyViewRunSummary>()
  for (const run of runs) {
    // Completed runs only; an in-progress re-run must not mask the failure it is retrying.
    if (run.status !== "completed") continue
    const key = `${run.repository}::${run.name ?? ""}`
    const seen = latest.get(key)
    if (!seen || run.created_at > seen.created_at) latest.set(key, run)
  }
  return [...latest.values()]
    .filter((r) => r.conclusion != null && FAILED_CONCLUSIONS.has(r.conclusion))
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
}

function InboxSection({ title, empty, children, count }: { title: string; empty: string; children: React.ReactNode; count: number }) {
  return (
    <section className="card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">{title}</span>
        <span className="stat-chip">{count}</span>
      </div>
      {count === 0 ? <p className="px-4 py-4 text-sm text-muted-foreground">{empty}</p> : <div className="divide-y divide-border">{children}</div>}
    </section>
  )
}

const rowClass = "flex items-center justify-between gap-3 px-4 py-2.5 text-sm hover:bg-elevated transition-colors"

/** Summary above My Work's tabs: reviews waiting on you (longest first) and your failing CI runs. */
export function DeveloperInbox({ org, token }: { org: string; token?: string }) {
  const { data } = useQuery<MyViewResponse>({
    queryKey: ["analytics.my-view", org],
    queryFn: () => api.analytics.myView(org, token),
    retry: false,
  })

  // Hidden while loading, on error, or when GitHub can't tell who the user is: the tabs below
  // already explain that, and an inbox full of false "nothing waiting" would mislead.
  if (!data || data.identity_unresolved) return null

  const reviews = [...data.review_requests]
    .sort((a, b) => (a.created_at ?? a.updated_at).localeCompare(b.created_at ?? b.updated_at))
    .slice(0, MAX_ROWS)
  const failing = latestFailingRuns(data.my_recent_runs)

  return (
    <div className="grid gap-4 md:grid-cols-2 mb-6">
      <InboxSection title="Reviews waiting on you" empty="No reviews waiting." count={data.review_requests.length}>
        {reviews.map((pr) => (
          <a key={`${pr.repository}-${pr.number}`} href={pr.html_url} target="_blank" rel="noreferrer" className={rowClass}>
            <span className="flex flex-col min-w-0">
              <span className="text-foreground/90 truncate">{pr.title}</span>
              <span className="text-[0.6875rem] text-muted-foreground font-mono">{pr.repository} #{pr.number}</span>
            </span>
            <span className="text-[0.6875rem] text-warning whitespace-nowrap shrink-0">
              opened {relativeTime(pr.created_at ?? pr.updated_at)}
            </span>
          </a>
        ))}
      </InboxSection>
      <InboxSection title="Your failing CI runs" empty="No failing runs." count={failing.length}>
        {failing.slice(0, MAX_ROWS).map((run) => (
          <a key={run.id} href={run.html_url} target="_blank" rel="noreferrer" className={rowClass}>
            <span className="flex flex-col min-w-0">
              <span className="text-foreground/90 truncate">{run.name ?? "Workflow run"}</span>
              <span className="text-[0.6875rem] text-muted-foreground font-mono">{run.repository}</span>
            </span>
            <span className="text-[0.6875rem] text-destructive whitespace-nowrap shrink-0">
              {run.conclusion} {relativeTime(run.created_at)}
            </span>
          </a>
        ))}
      </InboxSection>
    </div>
  )
}
