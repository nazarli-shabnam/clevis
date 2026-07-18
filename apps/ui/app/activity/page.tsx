"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { ActivityList } from "@/components/activity-list"
import { EventFeed } from "@/components/event-feed"
import { api } from "@/lib/api/client"

const EVENTS_REFRESH_SECONDS = 30

function useCountdown(resetKey: number | undefined, seconds: number): number {
  const [remaining, setRemaining] = useState(seconds)

  useEffect(() => {
    setRemaining(seconds)
  }, [resetKey, seconds])

  useEffect(() => {
    const interval = setInterval(() => {
      setRemaining((r) => (r > 0 ? r - 1 : 0))
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  return remaining
}

export default function ActivityPage() {
  const { data: jobs = [], isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 15_000,
  })

  const [org, setOrg] = useState("")
  useEffect(() => {
    setOrg(localStorage.getItem("default_org") || "")
  }, [])

  const resolveQuery = useQuery({
    queryKey: ["tokens.resolve", org],
    queryFn: () => api.tokens.resolve(org),
    enabled: org.trim().length > 2,
    retry: false,
  })

  const configured = !!resolveQuery.data?.token

  const eventsQuery = useQuery({
    queryKey: ["github.events", org],
    queryFn: () => api.github.events(org, resolveQuery.data!.token),
    enabled: configured,
    retry: false,
    refetchInterval: EVENTS_REFRESH_SECONDS * 1000,
  })

  const countdown = useCountdown(eventsQuery.dataUpdatedAt, EVENTS_REFRESH_SECONDS)

  return (
    <>
      <PageHeader
        title="Activity"
        description="Recent GitHub activity and background jobs."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2 bg-card border border-border">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <span className="section-label">Activity Feed</span>
            {configured && <span className="stat-chip">refreshes in {countdown}s</span>}
          </div>
          {!configured ? (
            <div className="px-4 py-8">
              <p className="text-sm text-muted-foreground">
                No organization configured yet.{" "}
                <Link href="/security" className="text-primary hover:underline">
                  Configure →
                </Link>
              </p>
            </div>
          ) : (
            <EventFeed events={eventsQuery.data?.events ?? []} isLoading={eventsQuery.isLoading} />
          )}
        </div>

        <div className="lg:col-span-1 bg-card border border-border">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <span className="section-label">Jobs</span>
            <span className="stat-chip">auto-refreshes every 15s</span>
          </div>
          <ActivityList jobs={jobs} isLoading={jobsLoading} />
        </div>
      </div>
    </>
  )
}
