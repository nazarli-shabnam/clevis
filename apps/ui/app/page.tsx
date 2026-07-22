"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { StatCard } from "@/components/stat-card"
import { EventActivityList } from "@/components/event-activity-list"
import { AreaTimeChart } from "@/components/charts/area-time-chart"
import { BarGroupChart } from "@/components/charts/bar-group-chart"
import { ArrowRight } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { CHART_COLORS } from "@/lib/charts/theme"

const quickActions = [
  { label: "Run Security Scan",  href: "/security" },
  { label: "Manage Caches",      href: "/repos" },
  { label: "View Collaborators", href: "/collaborators" },
]

function LiveStatCard({ label, loading, configured, value, trend }: {
  label: string
  loading: boolean
  configured: boolean
  value: number | undefined
  trend?: number[]
}) {
  if (!configured) {
    return (
      <Link href="/security" className="card px-4 py-4 block hover:bg-elevated transition-colors">
        <p className="telemetry-label mb-2 block">
          {label}
        </p>
        <p className="text-lg font-semibold text-foreground leading-none">
          Configure →
        </p>
      </Link>
    )
  }

  return <StatCard label={label} value={loading ? "…" : (value ?? "—")} trend={trend} />
}

export default function OverviewPage() {
  const [org, setOrg] = useState("")
  useEffect(() => {
    setOrg(localStorage.getItem("default_org") || "")
  }, [])

  // Not chained to the cockpit query below — both fire on mount. This one only
  // drives the "not configured yet" CTA-card branch, matching the pre-cockpit
  // behavior, so that empty state doesn't regress.
  const resolveQuery = useQuery({
    queryKey: ["tokens.resolve", org],
    queryFn: () => api.tokens.resolve(org),
    enabled: org.trim().length > 2,
    retry: false,
  })
  const configured = !!resolveQuery.data?.token

  // Both queries fire as soon as `org` is known (no waterfall) — cockpit just
  // waits for resolveQuery's fetch to settle (not to finish loading data through
  // a dependent chain) so a saved PAT isn't missed on the very first request
  // (queryKey excludes token, so a later-arriving token wouldn't otherwise
  // trigger a refetch of an already-fired query).
  const cockpitQuery = useQuery({
    queryKey: ["analytics.cockpit", org],
    queryFn: () => api.analytics.cockpit(org, resolveQuery.data?.token),
    enabled: org.trim().length > 2 && !resolveQuery.isLoading,
    retry: false,
    refetchInterval: 30_000,
  })
  const cockpit = cockpitQuery.data

  const commitActivityData = (cockpit?.commit_activity_4w ?? []).map((value, i) => ({
    week: `W${i + 1}`,
    value,
  }))
  const scoreTrendData = (cockpit?.score_trend ?? []).map((value, i) => ({
    week: `Scan ${i + 1}`,
    value,
  }))
  const prMergeRateData = (cockpit?.pr_merge_rate_4w ?? []).map((b) => ({
    name: b.week,
    opened: b.opened,
    merged: b.merged,
  }))

  return (
    <>
      <PageHeader title="Overview" description="Your GitHub organization at a glance." />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-px mb-6 border border-border bg-border">
        <LiveStatCard
          label="Repositories"
          loading={cockpitQuery.isLoading}
          configured={configured}
          value={cockpit?.repo_count}
        />
        <LiveStatCard
          label="Open PRs"
          loading={cockpitQuery.isLoading}
          configured={configured}
          value={cockpit?.open_pr_count}
        />
        <LiveStatCard
          label="Security Score"
          loading={cockpitQuery.isLoading}
          configured={configured}
          value={cockpit?.latest_score ?? undefined}
          trend={cockpit?.score_trend}
        />
        <LiveStatCard
          label="Team Members"
          loading={cockpitQuery.isLoading}
          configured={configured}
          value={cockpit?.member_count}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2 mb-6">
        <div className="card">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">Commit Activity (4w)</span>
          </div>
          <div className="p-4">
            {commitActivityData.length > 0 ? (
              <AreaTimeChart data={commitActivityData} label="Commits" color={CHART_COLORS.primary} height={200} />
            ) : (
              <p className="text-sm text-muted-foreground">No commit activity yet</p>
            )}
          </div>
        </div>

        <div className="card">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">Security Score</span>
          </div>
          <div className="p-4">
            {scoreTrendData.length > 1 ? (
              <AreaTimeChart data={scoreTrendData} label="Score" color={CHART_COLORS.series[1]} height={200} />
            ) : (
              <p className="text-sm text-muted-foreground">Run more scans to see a trend</p>
            )}
          </div>
        </div>

        <div className="card">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">PR Merge Rate (4w)</span>
          </div>
          <div className="p-4">
            {prMergeRateData.length > 0 ? (
              <BarGroupChart
                data={prMergeRateData}
                bars={[
                  { key: "opened", color: CHART_COLORS.primary },
                  { key: "merged", color: CHART_COLORS.series[1] },
                ]}
                height={200}
              />
            ) : (
              <p className="text-sm text-muted-foreground">No pull request activity yet</p>
            )}
          </div>
        </div>

        <div className="card">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">Recent Activity</span>
          </div>
          <EventActivityList
            events={cockpit?.recent_events ?? []}
            isLoading={cockpitQuery.isLoading}
            limit={5}
          />
        </div>
      </div>

      <div className="card mb-6">
        <div className="px-4 py-3 border-b border-border">
          <span className="section-label">Quick Actions</span>
        </div>
        <div className="p-2">
          {quickActions.map((action) => (
            <Link
              key={action.href}
              href={action.href}
              className="flex items-center justify-between px-3 py-2.5 text-sm text-muted-foreground hover:text-foreground hover:bg-elevated transition-colors group"
            >
              {action.label}
              <ArrowRight className="size-3.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
            </Link>
          ))}
        </div>
      </div>
    </>
  )
}
