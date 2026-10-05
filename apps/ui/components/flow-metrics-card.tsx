"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api/client"
import type { FlowWorkflowMetrics } from "@/lib/api/types"

/** "26.5" -> "1d 3h"; sub-day values stay in hours so a 4h review wait isn't rounded to "0d". */
export function formatHours(hours: number | null): string {
  if (hours == null) return "—"
  if (hours < 24) return `${Math.round(hours * 10) / 10}h`
  const days = Math.floor(hours / 24)
  const rest = Math.round(hours - days * 24)
  // 47.6h: the remainder rounds up to 24h, which is another whole day, not "1d 24h".
  if (rest === 24) return `${days + 1}d`
  return rest ? `${days}d ${rest}h` : `${days}d`
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—"
  return seconds < 90 ? `${seconds}s` : `${Math.round(seconds / 60)}m`
}

function WorkflowList({ title, rows, render }: { title: string; rows: FlowWorkflowMetrics[]; render: (w: FlowWorkflowMetrics) => string }) {
  return (
    <div>
      <p className="text-xs font-medium text-foreground mb-2">{title}</p>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nothing to flag.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map((w) => (
            <li key={w.name} className="flex justify-between gap-3 text-xs">
              <span className="truncate">{w.name}</span>
              <span className="font-mono text-muted-foreground whitespace-nowrap">{render(w)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * PR cycle time / review latency and workflow duration + flakiness for one repo. Loaded on
 * demand: it costs a handful of GitHub requests (the API caches the result for 10 minutes).
 *
 * Mount it with `key={owner/repo}`: `requested` is local state, so without a key, navigating to another
 * repo would keep it true and auto-fire the expensive request for that repo. `tokenReady` is false while
 * the caller is still resolving the saved token; a click before then waits for it instead of sending "".
 */
export function FlowMetricsCard({
  org,
  owner,
  repo,
  token,
  tokenReady = true,
}: {
  org: string
  owner: string
  repo: string
  token: string
  tokenReady?: boolean
}) {
  const [requested, setRequested] = useState(false)
  const query = useQuery({
    queryKey: ["repo-flow-metrics", org, owner, repo, token],
    queryFn: () => api.repos.flowMetrics(org, owner, repo, token),
    enabled: requested && tokenReady,
    retry: false,
  })
  const data = query.data

  const slowest = [...(data?.workflows ?? [])]
    .filter((w) => w.avg_duration_seconds != null)
    .sort((a, b) => b.avg_duration_seconds! - a.avg_duration_seconds!)
    .slice(0, 3)
  const flaky = [...(data?.workflows ?? [])]
    .filter((w) => w.flaky_commits > 0)
    .sort((a, b) => b.flaky_commits - a.flaky_commits)
    .slice(0, 3)

  return (
    <div className="card lg:col-span-2">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">Delivery flow{data ? ` · last ${data.window_days} days` : ""}</span>
        {!requested && (
          <Button size="sm" variant="outline" onClick={() => setRequested(true)}>
            Load metrics
          </Button>
        )}
        {query.isError && (
          <Button size="sm" variant="outline" onClick={() => query.refetch()} disabled={query.isFetching}>
            Retry
          </Button>
        )}
      </div>
      <div className="p-4">
        {!requested ? (
          <p className="text-sm text-muted-foreground">PR cycle time, review latency, and slow or flaky workflows.</p>
        ) : query.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : query.isError ? (
          <p className="text-xs text-destructive">{query.error.message}</p>
        ) : data ? (
          <div className="grid gap-5 md:grid-cols-3">
            <div className="flex flex-col gap-1.5 text-xs">
              <p className="text-foreground font-medium">Pull requests</p>
              <p>Merged: <span className="font-mono">{data.prs.merged_count}</span></p>
              <p>Median cycle time: <span className="font-mono">{formatHours(data.prs.median_cycle_hours)}</span></p>
              <p>Median time to first review: <span className="font-mono">{formatHours(data.prs.median_first_review_hours)}</span></p>
              <p className="text-muted-foreground">
                {data.prs.merged_without_review} of the last {data.prs.review_sample_size - data.prs.review_lookup_failed}{" "}
                merged had no review.
              </p>
              {data.prs.review_lookup_failed > 0 && (
                <p className="text-muted-foreground">
                  Reviews for {data.prs.review_lookup_failed} merged PR{data.prs.review_lookup_failed === 1 ? "" : "s"} could
                  not be loaded, so they are not counted either way.
                </p>
              )}
              {data.prs_truncated && (
                <p className="text-muted-foreground">
                  This repo has many merged PRs; PR figures cover only the most recent ones.
                </p>
              )}
            </div>
            <WorkflowList
              title="Slowest workflows"
              rows={slowest}
              render={(w) => `${formatDuration(w.avg_duration_seconds)} avg`}
            />
            <WorkflowList
              title="Flaky workflows"
              rows={flaky}
              render={(w) => `${w.flaky_commits} commit${w.flaky_commits === 1 ? "" : "s"}`}
            />
            {data.workflows_truncated && (
              <p className="text-xs text-muted-foreground md:col-span-3">
                This repo has many runs; workflow figures cover only the most recent ones.
              </p>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}
