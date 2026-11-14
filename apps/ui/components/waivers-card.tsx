"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { dueTime } from "@/lib/format"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { MAX_WAIVER_DAYS, WAIVABLE_CHECKS, WaiverForm, daysRemaining } from "@/components/waiver-form"
import type { CheckWaiver } from "@/lib/api/types"

/** Org admins record accepted risks: a failing check is kept out of the score until the waiver expires. */
export function WaiversCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["waivers", orgLogin]
  const query = useQuery<CheckWaiver[]>({ queryKey, queryFn: () => api.waivers.list(orgLogin), retry: false })
  // The waiver being renewed or edited; the form below switches from "add" to "save" for it.
  const [editing, setEditing] = useState<CheckWaiver | null>(null)
  // The waiver whose Revoke is awaiting confirmation.
  const [confirming, setConfirming] = useState<CheckWaiver | null>(null)

  const revoke = useMutation({
    mutationFn: (id: number) => api.waivers.revoke(orgLogin, id),
    onSuccess: (_data, id) => {
      // A revoked waiver can't be saved any more (PUT 404s), so drop the form's binding to it.
      setEditing((current) => (current?.id === id ? null : current))
      return queryClient.invalidateQueries({ queryKey })
    },
    onSettled: () => setConfirming(null),
  })

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
        <p className="px-4 py-3 text-xs text-muted-foreground">No waivers.</p>
      ) : (
        <ul className="divide-y divide-border">
          {query.data.map((w) => {
            const title = WAIVABLE_CHECKS[w.check_id] ?? w.check_id
            const days = daysRemaining(w.expires_at)
            return (
              <li key={w.id} className="px-4 py-2 flex items-start justify-between gap-3 text-xs">
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
