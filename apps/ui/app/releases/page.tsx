"use client"

import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { EmptyStateInline, EmptyStateNoAccount } from "@/components/empty-state"
import { SectionError } from "@/components/section-error"
import { DataTable, type DataTableColumn } from "@/components/ui/data-table"
import { relativeTime } from "@/lib/format"
import { api } from "@/lib/api/client"
import type { ReleaseSummary } from "@/lib/api/types"
import { useActiveScope } from "@/lib/active-scope"
import { hasOrgLogin } from "@/lib/token-resolve"

const DAY_OPTIONS = [30, 90, 180] as const

const COLUMNS: DataTableColumn<ReleaseSummary>[] = [
  {
    key: "repo",
    header: "Repository",
    sortValue: (r) => r.repo,
    render: (r) => <span className="text-muted-foreground">{r.repo}</span>,
    csvValue: (r) => r.repo,
  },
  {
    key: "release",
    header: "Release",
    sortValue: (r) => r.name,
    render: (r) => (
      <a href={r.url} target="_blank" rel="noreferrer" className="text-foreground/90 hover:text-primary transition-colors">
        {r.name}
      </a>
    ),
    csvValue: (r) => r.name,
  },
  {
    key: "tag",
    header: "Tag",
    sortValue: (r) => r.tag_name,
    render: (r) => <span className="font-mono text-muted-foreground">{r.tag_name}</span>,
    csvValue: (r) => r.tag_name,
  },
  {
    key: "notes",
    header: "Notes",
    cellClassName: "max-w-xs truncate text-muted-foreground",
    render: (r) => r.body_preview,
    csvValue: (r) => r.body_preview,
  },
  {
    key: "published",
    header: "Published",
    align: "right",
    sortValue: (r) => r.published_at,
    cellClassName: "whitespace-nowrap text-muted-foreground",
    render: (r) => relativeTime(r.published_at),
    csvValue: (r) => r.published_at,
  },
  {
    key: "type",
    header: "Type",
    sortValue: (r) => (r.is_prerelease ? 1 : 0),
    render: (r) => (r.is_prerelease ? <span className="stat-chip">pre-release</span> : <span className="text-muted-foreground">stable</span>),
    csvValue: (r) => (r.is_prerelease ? "pre-release" : "stable"),
  },
  {
    key: "url",
    header: "Link",
    render: (r) => (
      <a href={r.url} target="_blank" rel="noreferrer" aria-label={`Open ${r.repo} ${r.name} on GitHub`} className="text-primary underline">
        GitHub
      </a>
    ),
    csvValue: (r) => r.url,
  },
]

export default function ReleasesPage() {
  const { scope } = useActiveScope()
  // The release timeline is an org endpoint; a personal scope would 404.
  const org = scope?.kind === "org" ? scope.login : ""
  const [orgChecked, setOrgChecked] = useState(false)
  const [days, setDays] = useState<(typeof DAY_OPTIONS)[number]>(90)
  useEffect(() => {
    setOrgChecked(true)
  }, [])

  const resolveQuery = useQuery({
    queryKey: ["tokens.resolve", org],
    queryFn: () => api.tokens.resolve(org),
    enabled: hasOrgLogin(org),
    retry: false,
  })

  const token = resolveQuery.data?.token ?? ""
  const queriesEnabled = hasOrgLogin(org) && !resolveQuery.isLoading

  const releaseTimelineQuery = useQuery({
    queryKey: ["github.release-timeline", org, days],
    queryFn: () => api.github.releaseTimeline(org, token, days),
    enabled: queriesEnabled,
    retry: false,
  })

  const releases = releaseTimelineQuery.data?.releases ?? []

  return (
    <>
      <PageHeader title="Releases" description="Release history across your organization." />

      {orgChecked && !org && <EmptyStateNoAccount message={scope?.kind === "personal" ? "This view lists organization repositories. Pick an organization from the profile menu." : undefined} />}

      {org && (
        <div className="card">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-4">
            <span className="section-label">Releases</span>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value) as (typeof DAY_OPTIONS)[number])}
              aria-label="Release time range"
              className="bg-elevated border border-border rounded-md text-xs text-muted-foreground font-mono px-2 py-1 focus:outline-none focus:border-primary"
            >
              {DAY_OPTIONS.map((d) => (
                <option key={d} value={d}>last {d} days</option>
              ))}
            </select>
          </div>

          {releaseTimelineQuery.isLoading || resolveQuery.isLoading ? (
            <div className="px-4 py-8">
              <p className="text-sm text-muted-foreground animate-pulse">Loading…</p>
            </div>
          ) : releaseTimelineQuery.isError ? (
            <SectionError
              message={
                releaseTimelineQuery.error instanceof Error
                  ? releaseTimelineQuery.error.message
                  : "Failed to load releases."
              }
              onRetry={() => releaseTimelineQuery.refetch()}
              retrying={releaseTimelineQuery.isFetching}
            />
          ) : releases.length === 0 ? (
            <EmptyStateInline noun={`releases in the last ${days} days`} />
          ) : (
            <DataTable
              columns={COLUMNS}
              data={releases}
              getRowKey={(r) => r.url}
              exportCsv={{ name: `releases-${org}-${days}d` }}
            />
          )}
        </div>
      )}
    </>
  )
}
