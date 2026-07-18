"use client"

import { useEffect, useRef } from "react"
import { useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { EmptyStateInline } from "@/components/empty-state"
import { api } from "@/lib/api/client"
import type { JobOut } from "@/lib/api/types"

export default function JobsPage() {
  const searchParams = useSearchParams()
  const highlightId = Number(searchParams.get("id")) || null

  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 10_000,
  })

  const highlightRef = useRef<HTMLTableRowElement>(null)

  // Scroll the highlighted row into view when the target job is rendered
  // Depends on highlightId (query param changes) and jobs.length (data arrives)
  useEffect(() => {
    if (highlightRef.current) {
      highlightRef.current.scrollIntoView({ block: "center", behavior: "smooth" })
    }
  }, [highlightId, jobs.length])

  const statusColor: Record<JobOut["status"], string> = {
    queued:     "text-muted-foreground",
    processing: "text-yellow-400",
    done:       "text-accent",
    failed:     "text-destructive",
  }

  return (
    <>
      <PageHeader title="Job Queue" description="Background job status and history." />

      <div className="card">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="section-label">Jobs</span>
          {!isLoading && (
            <span className="stat-chip">{jobs.length} total</span>
          )}
        </div>

        {isLoading ? (
          <div className="px-4 py-8">
            <p className="text-sm text-muted-foreground animate-pulse">Loading…</p>
          </div>
        ) : jobs.length === 0 ? (
          <EmptyStateInline noun="jobs" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left section-label px-4 py-2">ID</th>
                  <th className="text-left section-label px-4 py-2">Type</th>
                  <th className="text-left section-label px-4 py-2">Status</th>
                  <th className="text-left section-label px-4 py-2">Result</th>
                  <th className="text-right section-label px-4 py-2">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.map((job) => {
                  const isHighlighted = highlightId !== null && job.id === highlightId
                  return (
                    <tr
                      key={job.id}
                      ref={isHighlighted ? highlightRef : null}
                      className={[
                        "hover:bg-elevated transition-colors",
                        isHighlighted ? "ring-1 ring-inset ring-primary/40 bg-primary/5" : "",
                      ].join(" ")}
                    >
                      <td className="px-4 py-2.5 font-mono text-muted-foreground">#{job.id}</td>
                      <td className="px-4 py-2.5 text-foreground/80">{job.job_type}</td>
                      <td className={`px-4 py-2.5 font-mono font-medium ${statusColor[job.status]}`}>
                        {job.status}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground max-w-[16rem] truncate">
                        {job.result ?? "—"}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-muted-foreground whitespace-nowrap">
                        {new Date(job.created_at).toLocaleString()}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
