"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { SectionError } from "@/components/section-error"
import type { ScheduledScanSettings } from "@/lib/api/types"

type Choice = "follow" | "on" | "off"

const toChoice = (enabled: boolean | null): Choice => (enabled === null ? "follow" : enabled ? "on" : "off")
const fromChoice = (choice: Choice): boolean | null => (choice === "follow" ? null : choice === "on")

/** Org admins choose whether this org is re-scanned automatically, overriding the instance cadence. */
export function ScheduledScansCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["scheduled-scans", orgLogin]
  const query = useQuery<ScheduledScanSettings>({
    queryKey,
    queryFn: () => api.orgs.scheduledScans(orgLogin),
    retry: false,
  })
  const update = useMutation({
    mutationFn: (choice: Choice) => api.orgs.setScheduledScans(orgLogin, fromChoice(choice)),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Scheduled scans</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load the scheduled-scan settings: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-2">
          <label htmlFor="scheduled-scans" className="text-xs font-medium text-foreground">
            Scan this organization automatically
          </label>
          <select
            id="scheduled-scans"
            value={toChoice(query.data.enabled)}
            disabled={update.isPending}
            onChange={(e) => update.mutate(e.target.value as Choice)}
            className="text-xs card text-foreground px-2 py-1.5 max-w-72 focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="follow">Follow the instance setting ({query.data.instance_cadence})</option>
            <option value="on">Always scan on a schedule</option>
            <option value="off">Never scan on a schedule</option>
          </select>
          <p className="text-xs text-muted-foreground">
            {query.data.effective
              ? `Re-scanned ${query.data.cadence} using the GitHub App installation, so a score drop is noticed without anyone clicking Scan.`
              : "Not scanned automatically: scores only change when someone runs a scan."}{" "}
            Needs the GitHub App installed; a pasted token is never used for scheduled scans.
          </p>
          {update.isError && <p className="text-xs text-destructive">{update.error.message}</p>}
        </div>
      )}
    </div>
  )
}
