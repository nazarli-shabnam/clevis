"use client"

import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch, DownloadSimple } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { toCsv } from "@/lib/csv"
import { downloadTextFile } from "@/lib/download"
import { dueTime } from "@/lib/format"
import { EmptyStateAction } from "@/components/empty-state-action"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { MAX_WAIVER_DAYS, WAIVABLE_CHECKS, WaiverForm, daysRemaining } from "@/components/waiver-form"
import type { CheckWaiver } from "@/lib/api/types"

const EXPIRING_SOON_DAYS = 14

type WaiverFilter = "all" | "active" | "expiring" | "expired"
const FILTER_LABELS: Record<WaiverFilter, string> = {
  all: "All",
  active: "Active",
  expiring: `Expiring in ${EXPIRING_SOON_DAYS} days`,
  expired: "Expired",
}

function matchesFilter(w: CheckWaiver, filter: WaiverFilter): boolean {
  if (filter === "all") return true
  if (filter === "expired") return !w.active
  if (filter === "active") return w.active
  return w.active && daysRemaining(w.expires_at) <= EXPIRING_SOON_DAYS
}

/** Org admins record accepted risks: a failing check is kept out of the score until the waiver expires. */
export function WaiversCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["waivers", orgLogin]
  const query = useQuery<CheckWaiver[]>({ queryKey, queryFn: () => api.waivers.list(orgLogin), retry: false })
  // The waiver being renewed or edited; the form below switches from "add" to "save" for it.
  const [editing, setEditing] = useState<CheckWaiver | null>(null)
  // The waiver whose Revoke is awaiting confirmation.
  const [confirming, setConfirming] = useState<CheckWaiver | null>(null)

  // A notification links here with ?waiver=<id>: that row is marked and scrolled into view.
  const linkedWaiver = Number(useSearchParams()?.get("waiver")) || null
  const loadedCount = query.data?.length
  useEffect(() => {
    if (linkedWaiver !== null) document.getElementById(`waiver-${linkedWaiver}`)?.scrollIntoView?.({ block: "center" })
  }, [linkedWaiver, loadedCount])
  const [filter, setFilter] = useState<WaiverFilter>("all")

  const revoke = useMutation({
    mutationFn: (id: number) => api.waivers.revoke(orgLogin, id),
    onSuccess: (_data, id) => {
      // A revoked waiver can't be saved any more (PUT 404s), so drop the form's binding to it.
      setEditing((current) => (current?.id === id ? null : current))
      return queryClient.invalidateQueries({ queryKey })
    },
    onSettled: () => setConfirming(null),
  })

  const visible = (query.data ?? []).filter((w) => matchesFilter(w, filter))

  // The register as an auditor reads it, for the rows currently shown.
  function exportCsv(rows: CheckWaiver[]) {
    const csv = toCsv(rows, [
      { header: "Check", value: (w) => w.check_id },
      { header: "Repositories", value: (w) => (w.repos ? w.repos.join("; ") : "whole organization") },
      { header: "Reason", value: (w) => w.reason },
      { header: "Owner", value: (w) => w.owner },
      { header: "Created by", value: (w) => w.created_by },
      { header: "Created", value: (w) => w.created_at },
      { header: "Expires", value: (w) => w.expires_at },
      { header: "Status", value: (w) => (w.active ? "active" : "expired") },
    ])
    downloadTextFile(`clevis-waivers-${orgLogin}-${new Date().toISOString().slice(0, 10)}.csv`, csv, "text/csv")
  }

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Check waivers</span>
      </div>
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Record an accepted risk: the failing check stays visible but is left out of the score until the waiver expires
        (at most {MAX_WAIVER_DAYS} days). Every change is written to the audit log.
      </p>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load waivers: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.data.length === 0 ? (
        <EmptyStateAction
          message="No waivers. Record an accepted risk so a check you've chosen to live with stops lowering the score until it expires."
          actionLabel="Add a waiver"
          focusId="waiver-new-check"
        />
      ) : (
        <>
        <div className="px-4 py-2 flex flex-wrap items-center gap-2 text-xs">
          <label className="flex items-center gap-1.5 text-muted-foreground">
            Show
            <select
              aria-label="Filter waivers"
              value={filter}
              onChange={(e) => setFilter(e.target.value as WaiverFilter)}
              className="card text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
            >
              {(Object.keys(FILTER_LABELS) as WaiverFilter[]).map((f) => (
                <option key={f} value={f}>{FILTER_LABELS[f]}</option>
              ))}
            </select>
          </label>
          <span className="text-muted-foreground">{visible.length} of {query.data.length}</span>
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            disabled={visible.length === 0}
            onClick={() => exportCsv(visible)}
          >
            <DownloadSimple className="size-3.5" /> Export CSV
          </Button>
        </div>
        {visible.length === 0 && <p className="px-4 pb-3 text-xs text-muted-foreground">No waivers match this filter.</p>}
        <ul className="divide-y divide-border">
          {visible.map((w) => {
            const title = WAIVABLE_CHECKS[w.check_id] ?? w.check_id
            const days = daysRemaining(w.expires_at)
            return (
              <li
                key={w.id}
                id={`waiver-${w.id}`}
                aria-current={w.id === linkedWaiver ? "true" : undefined}
                className={`px-4 py-2 flex items-start justify-between gap-3 text-xs ${w.id === linkedWaiver ? "bg-elevated outline outline-1 outline-primary" : ""}`}
              >
                <div className="min-w-0">
                  <p className="font-medium text-foreground">
                    {title}
                    <span className="ml-2 text-muted-foreground font-normal">{w.repos ? w.repos.join(", ") : "whole organization"}</span>
                  </p>
                  <p className="text-muted-foreground">
                    {w.reason} · owner {w.owner} ·{" "}
                    {w.active ? (
                      <>
                        expires {dueTime(w.expires_at)} · {days} {days === 1 ? "day" : "days"} left
                      </>
                    ) : (
                      <span className="text-destructive">expired {dueTime(w.expires_at)}</span>
                    )}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditing(w)}
                    aria-label={`Renew or edit waiver for ${title}`}
                  >
                    {w.active ? "Edit" : "Renew"}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={revoke.isPending}
                    onClick={() => setConfirming(w)}
                    aria-label={`Revoke waiver for ${title}`}
                  >
                    Revoke
                  </Button>
                </div>
              </li>
            )
          })}
        </ul>
        </>
      )}
      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => !open && setConfirming(null)}
        title={`Revoke the waiver for ${confirming ? (WAIVABLE_CHECKS[confirming.check_id] ?? confirming.check_id) : ""}?`}
        description="The check counts toward the score again from the next scan. This can't be undone."
        confirmLabel="Revoke"
        pending={revoke.isPending}
        onConfirm={() => confirming && revoke.mutate(confirming.id)}
      />
      {revoke.isError && (
        <p role="alert" className="px-4 pb-2 text-xs text-destructive">
          Couldn&apos;t revoke: {revoke.error.message}
        </p>
      )}

      <WaiverForm
        key={editing?.id ?? "new"}
        orgLogin={orgLogin}
        editing={editing ?? undefined}
        onSaved={() => setEditing(null)}
        onCancel={editing ? () => setEditing(null) : undefined}
      />
    </div>
  )
}
