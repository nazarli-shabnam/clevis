"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { StatCard } from "@/components/stat-card"
import { ActivityList } from "@/components/activity-list"
import { ArrowRight, Warning } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"

const quickActions = [
  { label: "Run Security Scan",  href: "/security" },
  { label: "Manage Caches",      href: "/repos" },
  { label: "View Collaborators", href: "/collaborators" },
]

function LiveStatCard({ label, loading, configured, value }: {
  label: string
  loading: boolean
  configured: boolean
  value: number | undefined
}) {
  if (!configured) {
    // Post-PAT: configuring means connecting a GitHub App + setting default org.
    return (
      <Link href="/settings" className="bg-card border border-border px-4 py-4 block hover:bg-elevated transition-colors">
        <p className="text-[0.6875rem] font-medium font-mono text-muted-foreground uppercase tracking-[0.1em] mb-3">
          {label}
        </p>
        <p className="text-lg font-semibold font-mono text-foreground leading-none">
          Configure →
        </p>
      </Link>
    )
  }

  return <StatCard label={label} value={loading ? "…" : (value ?? "—")} />
}

export default function OverviewPage() {
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 15_000,
  })

  const [org, setOrg] = useState("")
  useEffect(() => {
    setOrg(localStorage.getItem("default_org") || "")
  }, [])

  // Stats use the GitHub App installation token server-side — no legacy PAT resolve.
  const configured = org.trim().length > 2

  const overviewQuery = useQuery({
    queryKey: ["analytics.overview", org],
    queryFn: () => api.analytics.overview(org),
    enabled: configured,
    retry: false,
  })

  return (
    <>
      <PageHeader title="Overview" description="Your GitHub organization at a glance." />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-px mb-6 border border-border bg-border">
        <LiveStatCard
          label="Repositories"
          loading={overviewQuery.isLoading}
          configured={configured}
          value={overviewQuery.data?.repo_count}
        />
        <StatCard label="Open PRs" value="N/A" />
        <LiveStatCard
          label="Security Score"
          loading={overviewQuery.isLoading}
          configured={configured}
          value={overviewQuery.data?.score}
        />
        <StatCard label="Team Members" value="N/A" />
      </div>

      {overviewQuery.isError && (
        <div className="mb-6 flex items-start gap-2 border border-destructive/30 bg-card px-4 py-3 text-xs text-destructive">
          <Warning className="size-3.5 mt-0.5 shrink-0" />
          <div>
            <p>{overviewQuery.error.message}</p>
            <Link href="/settings" className="text-primary hover:underline mt-1 inline-block">
              Connect a GitHub App in Settings →
            </Link>
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2 bg-card border border-border">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">Recent Activity</span>
          </div>
          <ActivityList jobs={jobs} isLoading={isLoading} limit={5} />
        </div>

        <div className="bg-card border border-border">
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
      </div>
    </>
  )
}
