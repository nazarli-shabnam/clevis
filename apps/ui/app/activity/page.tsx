"use client"

import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { ActivityList } from "@/components/activity-list"
import { api } from "@/lib/api/client"

export default function ActivityPage() {
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 15_000,
  })

  return (
    <>
      <PageHeader
        title="Activity"
        description="Background jobs and recent actions."
      />

      <div className="bg-card border border-border">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="section-label">Jobs</span>
          <span className="stat-chip">auto-refreshes every 15s</span>
        </div>
        <ActivityList jobs={jobs} isLoading={isLoading} />
      </div>
    </>
  )
}
