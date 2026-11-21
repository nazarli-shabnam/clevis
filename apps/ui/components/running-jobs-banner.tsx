"use client"

import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { awaitJob } from "@/lib/api/client"
import { toast } from "@/components/ui/toast"
import { isLive, listTrackedJobs, forgetJob, markLive, unmarkLive, type TrackedJob } from "@/lib/running-jobs"

/** Re-attaches to operations started before a reload, tab close or navigation: shows that they are still
 * running, and when one finishes reports it and refreshes the data it changed. Operations this page is already
 * waiting on are skipped (their own caller handles them). */
export function RunningJobsBanner() {
  const queryClient = useQueryClient()
  const [running, setRunning] = useState<TrackedJob[]>([])

  useEffect(() => {
    const controller = new AbortController()
    const attached = listTrackedJobs().filter((j) => !isLive(j.jobId))
    setRunning(attached)

    for (const job of attached) {
      markLive(job.jobId)
      const where = job.org ? ` for ${job.org}` : ""
      awaitJob(job.jobId, { signal: controller.signal })
        .then(() => {
          forgetJob(job.jobId)
          toast.success(`The ${job.label}${where} finished.`)
          // It changed server-side state the open pages may be showing: scans, scores, alerts, repos.
          void queryClient.invalidateQueries()
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return // unmounted: try again next load
          // Only a job that is still (possibly) running stays tracked; a failed or unknown one is finished.
          if (err instanceof Error && err.name !== "JobStillRunningError") {
            forgetJob(job.jobId)
            if (!/unknown job/i.test(err.message)) toast.error(`The ${job.label}${where} failed: ${err.message}`)
          }
        })
        .finally(() => {
          // After cleanup the marker is already released (and a remount may own it now): leave it alone.
          if (controller.signal.aborted) return
          unmarkLive(job.jobId)
          setRunning((prev) => prev.filter((j) => j.jobId !== job.jobId))
        })
    }
    return () => {
      controller.abort()
      // Released synchronously so a Strict Mode remount can attach these jobs again.
      for (const job of attached) unmarkLive(job.jobId)
    }
    // Once per mount: the list is read from storage, not from props.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (running.length === 0) return null
  return (
    <div role="status" className="card mb-4 px-4 py-3 flex flex-col gap-1 text-sm text-muted-foreground">
      {running.map((j) => (
        <p key={j.jobId} className="flex items-center gap-2">
          <CircleNotch className="size-3.5 animate-spin" />
          A {j.label}
          {j.org ? ` for ${j.org}` : ""} is still running. This page will refresh when it finishes.
        </p>
      ))}
    </div>
  )
}
