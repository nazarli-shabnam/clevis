"use client"

import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { openActivityStream } from "@/lib/api/client"
import { readSse } from "@/lib/sse"
import type { ActivitySummary } from "@/lib/api/types"

export type StreamStatus = "connecting" | "live" | "offline"

export const activitySummaryKey = (org: string, days: number) => ["github.activity-summary", org, days] as const

const CLEAN_END_RECONNECT_MS = 1_000
const RECONNECT_BASE_MS = 2_000
const RECONNECT_MAX_MS = 30_000

/** Keeps the cached activity summary for `org` fresh from the server's SSE stream and reports whether
 * it is live. The server ends each stream after ~15 minutes, so this reconnects (after a brief pause on a
 * clean end, with backoff after an error), and tears down on scope change, disable or unmount. */
export function useActivityStream(org: string, days: number, enabled: boolean): StreamStatus {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<StreamStatus>("connecting")

  useEffect(() => {
    if (!enabled || !org) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let failures = 0

    const run = async () => {
      setStatus("connecting")
      try {
        const res = await openActivityStream(org, days, controller.signal)
        if (!res.ok) throw new Error(`stream responded ${res.status}`)
        setStatus("live")
        await readSse(res, (m) => {
          if (m.event !== "activity_summary") return
          try {
            queryClient.setQueryData<ActivitySummary>(activitySummaryKey(org, days), JSON.parse(m.data))
            failures = 0
          } catch {
            // A malformed frame is skipped; the next change event replaces it.
          }
        })
        // Clean end: normally the server's duration cap. A short pause keeps a stream that closes
        // immediately, every time, from becoming a tight reconnect loop.
        if (!controller.signal.aborted) timer = setTimeout(run, CLEAN_END_RECONNECT_MS)
      } catch {
        if (controller.signal.aborted) return
        setStatus("offline")
        failures += 1
        timer = setTimeout(run, Math.min(RECONNECT_BASE_MS * 2 ** (failures - 1), RECONNECT_MAX_MS))
      }
    }
    void run()

    return () => {
      controller.abort()
      if (timer) clearTimeout(timer)
    }
  }, [org, days, enabled, queryClient])

  return status
}
