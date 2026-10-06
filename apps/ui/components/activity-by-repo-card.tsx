"use client"

import { useQuery } from "@tanstack/react-query"
import { SectionError } from "@/components/section-error"
import { api } from "@/lib/api/client"
import { activitySummaryKey, useActivityStream, type StreamStatus } from "@/lib/use-activity-stream"
import type { ActivitySummary } from "@/lib/api/types"

const DAYS = 7
const TOP_REPOS = 8

export interface RepoActivity {
  repo: string
  total: number
  byType: [string, number][]
}

/** Per-repo totals from the rollup rows, busiest repo first (ties by name), each with its event types
 * busiest first. */
export function topRepos(summary: ActivitySummary, limit = TOP_REPOS): RepoActivity[] {
  const byRepo = new Map<string, Map<string, number>>()
  for (const { repo, event_type, count } of summary.totals) {
    const types = byRepo.get(repo) ?? new Map<string, number>()
    types.set(event_type, (types.get(event_type) ?? 0) + count)
    byRepo.set(repo, types)
  }
  return [...byRepo.entries()]
    .map(([repo, types]) => ({
      repo,
      total: [...types.values()].reduce((a, b) => a + b, 0),
      byType: [...types.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
    }))
    .sort((a, b) => b.total - a.total || a.repo.localeCompare(b.repo))
    .slice(0, limit)
}

const STATUS_LABEL: Record<StreamStatus, string> = {
  live: "Live",
  connecting: "Connecting…",
  offline: "Reconnecting…",
}

export function ActivityByRepoCard({ org }: { org: string }) {
  const query = useQuery({
    queryKey: activitySummaryKey(org, DAYS),
    queryFn: () => api.github.activitySummary(org, DAYS),
    enabled: org.trim().length > 0,
    retry: false,
  })
  // Only stream once the snapshot says webhook data exists: an unconnected org has nothing to push.
  const status = useActivityStream(org, DAYS, query.data?.connected === true)
  const rows = query.data ? topRepos(query.data) : []

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <span className="section-label">Activity by repo (last {DAYS} days)</span>
        {query.data?.connected && (
          <span
            className={`stat-chip ${status === "live" ? "text-accent" : "text-muted-foreground"}`}
            title="Updates as GitHub webhooks arrive, without calling GitHub."
          >
            <span aria-hidden className={`inline-block size-1.5 rounded-full mr-1.5 ${status === "live" ? "bg-accent" : "bg-muted-foreground"}`} />
            {STATUS_LABEL[status]}
          </span>
        )}
      </div>
      {query.isError ? (
        <SectionError
          message={query.error instanceof Error ? query.error.message : "Failed to load activity summary."}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.isLoading ? (
        <p className="p-4 text-sm text-muted-foreground">Loading…</p>
      ) : !query.data?.connected ? (
        <p className="p-4 text-sm text-muted-foreground">
          Live activity needs the GitHub App installed on {org}: it reports events to Clevis as they happen, so no
          GitHub token or per-repo calls are needed. The feed above still works without it.
        </p>
      ) : rows.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No events recorded in the last {DAYS} days.</p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((r) => (
            <li key={r.repo} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
              <span className="flex flex-col min-w-0">
                <span className="text-foreground/90 truncate">{r.repo}</span>
                <span className="text-[0.6875rem] text-muted-foreground truncate">
                  {r.byType.map(([type, n]) => `${type} ${n}`).join(" · ")}
                </span>
              </span>
              <span className="stat-chip shrink-0">{r.total}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
