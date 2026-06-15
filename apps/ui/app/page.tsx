"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { StatCard } from "@/components/stat-card"
import { ActivityList } from "@/components/activity-list"
import { ArrowRight } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"

const stats = [
  { label: "Repositories",   value: "—" },
  { label: "Open PRs",       value: "—" },
  { label: "Security Score", value: "—" },
  { label: "Team Members",   value: "—" },
]

const quickActions = [
  { label: "Run Security Scan",  href: "/security" },
  { label: "Manage Caches",      href: "/repos" },
  { label: "View Collaborators", href: "/collaborators" },
]

export default function OverviewPage() {
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 15_000,
  })

  return (
    <>
      <PageHeader title="Overview" description="Your GitHub organization at a glance." />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-px mb-6 border border-border bg-border">
        {stats.map((s) => (
          <StatCard key={s.label} label={s.label} value={s.value} />
        ))}
      </div>

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
