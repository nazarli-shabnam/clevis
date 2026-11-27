"use client"

import { useEffect } from "react"
import { useSearchParams } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Time } from "@/components/time"
import { invalidateWaivers } from "@/components/waiver-form"
import { useWaivableChecks } from "@/lib/use-waivable-checks"
import type { WaiverRequest } from "@/lib/api/types"

/** Org admins: waiver requests members have sent. Approving creates the waiver (owner = the requester, expiry as
 * requested); denying closes the request. Renders nothing while there is none waiting. */
export function WaiverRequestsCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const { titleOf } = useWaivableChecks(orgLogin)
  const queryKey = ["waiver-requests", orgLogin, "pending"]
  const query = useQuery<WaiverRequest[]>({ queryKey, queryFn: () => api.waiverRequests.list(orgLogin, "pending"), retry: false })
  // A notification links here with ?waiver_request=<id>: that row is marked and scrolled into view.
  const linked = Number(useSearchParams()?.get("waiver_request")) || null
  const linkedLoaded = linked !== null && !!query.data?.some((r) => r.id === linked)
  useEffect(() => {
    if (linkedLoaded) document.getElementById(`waiver-request-${linked}`)?.scrollIntoView?.({ block: "center" })
  }, [linked, linkedLoaded])

  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: number; approve: boolean }) =>
      approve ? api.waiverRequests.approve(orgLogin, id) : api.waiverRequests.deny(orgLogin, id),
    onSettled: () =>
      Promise.all([queryClient.invalidateQueries({ queryKey: ["waiver-requests", orgLogin] }), invalidateWaivers(queryClient, orgLogin)]),
  })

  const pending = query.data ?? []
  if (pending.length === 0) return null

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Waiver requests</span>
        <span className="stat-chip ml-2">{pending.length} waiting</span>
      </div>
      <ul className="divide-y divide-border">
        {pending.map((r) => {
          const title = titleOf(r.check_id)
          return (
            <li
              key={r.id}
              id={`waiver-request-${r.id}`}
              aria-current={r.id === linked ? "true" : undefined}
              className={`px-4 py-2 flex items-start justify-between gap-3 text-xs ${r.id === linked ? "bg-elevated outline outline-1 outline-primary" : ""}`}
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium text-foreground">
                  {r.requested_by} asked to waive {title}
                  <span className="ml-2 text-muted-foreground font-normal">{r.repos ? r.repos.join(", ") : "whole organization"}</span>
                </p>
                <p className="text-muted-foreground">
                  {r.reason} · until <Time iso={r.requested_expires_at} due />
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  size="sm"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: r.id, approve: true })}
                  aria-label={`Approve the request to waive ${title}`}
                >
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={decide.isPending}
                  onClick={() => decide.mutate({ id: r.id, approve: false })}
                  aria-label={`Deny the request to waive ${title}`}
                >
                  Deny
                </Button>
              </div>
            </li>
          )
        })}
      </ul>
      {decide.isError && (
        <p role="alert" className="px-4 pb-2 text-xs text-destructive">
          Couldn&apos;t save the decision: {decide.error.message}
        </p>
      )}
    </div>
  )
}
