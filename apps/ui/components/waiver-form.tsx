"use client"

import { useState } from "react"
import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useWaivableChecks } from "@/lib/use-waivable-checks"
import type { CheckWaiver } from "@/lib/api/types"

export const MAX_WAIVER_DAYS = 90

/** Refresh everything a waiver write changes: the org's waiver list and each repository page's "Waived until" badge. */
export function invalidateWaivers(queryClient: QueryClient, orgLogin: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ["waivers", orgLogin] }),
    queryClient.invalidateQueries({ queryKey: ["repo-detail-waivers"] }),
  ])
}

/** A local calendar day as the YYYY-MM-DD an <input type="date"> uses (not the UTC day toISOString gives). */
const localDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
const dayInput = (days: number) => localDay(new Date(Date.now() + days * 86_400_000))

/** The latest day an admin can pick: the waiver expires at 23:59:59 local on the chosen day and the API
 * allows at most MAX_WAIVER_DAYS from now, so take the limit's local day, or the day before if its end is past it. */
function latestExpiryDay(): string {
  const limit = new Date(Date.now() + MAX_WAIVER_DAYS * 86_400_000)
  const endOfLimitDay = new Date(limit.getFullYear(), limit.getMonth(), limit.getDate(), 23, 59, 59)
  return endOfLimitDay.getTime() <= limit.getTime() ? localDay(limit) : localDay(new Date(limit.getTime() - 86_400_000))
}

/** Whole days until `iso` (rounded up, so "later today" is 1), or 0 once it has passed. */
export function daysRemaining(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000))
}

/** Where a renewal starts: the current end day while that is still pickable, else 30 days from now. */
function renewalDay(w: CheckWaiver): string {
  const current = localDay(new Date(w.expires_at))
  return w.active && current >= dayInput(1) && current <= latestExpiryDay() ? current : dayInput(30)
}

interface WaiverFormProps {
  orgLogin: string
  /** An existing waiver to renew or edit (saved with PUT); omit to add one. */
  editing?: CheckWaiver
  /** Waive exactly this check (the check picker is hidden), e.g. from a failing check's card. */
  fixedCheckId?: string
  /** Repos to prefill the repository list with (e.g. the ones a failing check names); ignored when editing. */
  initialRepos?: string[]
  /** One column, for narrow containers such as a check card. */
  compact?: boolean
  onSaved?: () => void
  onCancel?: () => void
}

/** Add a waiver, or renew/edit one: a failing check is kept out of the score until it expires. */
export function WaiverForm({ orgLogin, editing, fixedCheckId, initialRepos, compact, onSaved, onCancel }: WaiverFormProps) {
  const queryClient = useQueryClient()
  const { checks, loaded, isError, isOrgWideOnly } = useWaivableChecks(orgLogin)
  // The admin's pick, else the waiver being edited / the fixed check, else the first check that can be
  // scoped to repositories (the common case), else the first one the API offers.
  const [picked, setPicked] = useState<string>()
  const checkId = picked ?? editing?.check_id ?? fixedCheckId ?? (checks.find((c) => !c.org_wide_only) ?? checks[0])?.id ?? ""
  const [repos, setRepos] = useState(editing ? (editing.repos?.join(", ") ?? "") : (initialRepos?.join(", ") ?? ""))
  const [reason, setReason] = useState(editing?.reason ?? "")
  const [owner, setOwner] = useState(editing?.owner ?? "")
  const [until, setUntil] = useState(editing ? renewalDay(editing) : dayInput(30))

  const orgWideOnly = isOrgWideOnly(checkId)

  const save = useMutation({
    mutationFn: () => {
      const body = {
        check_id: checkId,
        repos: orgWideOnly ? [] : repos.split(",").map((r) => r.trim()).filter(Boolean),
        reason,
        owner,
        // End of the chosen day, local time.
        expires_at: new Date(`${until}T23:59:59`).toISOString(),
      }
      return editing ? api.waivers.update(orgLogin, editing.id, body) : api.waivers.create(orgLogin, body)
    },
    onSuccess: async () => {
      setReason("")
      setRepos("")
      await invalidateWaivers(queryClient, orgLogin)
      onSaved?.()
    },
  })

  const canSubmit = loaded && checkId !== "" && reason.trim() !== "" && owner.trim() !== "" && until !== "" && !save.isPending
  const idPrefix = `waiver-${editing?.id ?? fixedCheckId ?? "new"}`

  return (
    <form
      className={compact ? "grid gap-3" : "border-t border-border p-4 grid gap-3 sm:grid-cols-2"}
      onSubmit={(e) => {
        e.preventDefault()
        if (canSubmit) save.mutate()
      }}
    >
      {editing && (
        <p className={compact ? "text-xs font-medium" : "sm:col-span-2 text-xs font-medium"}>
          Renew or edit this waiver. Saving restarts its expiry notice.
        </p>
      )}
      {!fixedCheckId && (
        <div>
          <label htmlFor={`${idPrefix}-check`} className="text-xs font-medium block mb-1.5">Check</label>
          <select
            id={`${idPrefix}-check`}
            value={checkId}
            onChange={(e) => setPicked(e.target.value)}
            className="text-xs card text-foreground px-2 py-1.5 w-full focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {checks.map(({ id, title }) => (
              <option key={id} value={id}>{title}</option>
            ))}
          </select>
        </div>
      )}
      {orgWideOnly ? (
        <p className="text-xs text-muted-foreground">This check does not report which repositories fail, so the waiver covers the whole organization.</p>
      ) : (
        <div>
          <label htmlFor={`${idPrefix}-repos`} className="text-xs font-medium block mb-1.5">Repositories (optional)</label>
          <Input id={`${idPrefix}-repos`} placeholder="legacy-api, old-site — blank = whole organization" value={repos} onChange={(e) => setRepos(e.target.value)} />
        </div>
      )}
      <div>
        <label htmlFor={`${idPrefix}-reason`} className="text-xs font-medium block mb-1.5">Reason</label>
        <Input id={`${idPrefix}-reason`} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <div>
        <label htmlFor={`${idPrefix}-owner`} className="text-xs font-medium block mb-1.5">Owner</label>
        <Input id={`${idPrefix}-owner`} placeholder="person or team accountable" maxLength={100} value={owner} onChange={(e) => setOwner(e.target.value)} />
      </div>
      <div>
        <label htmlFor={`${idPrefix}-until`} className="text-xs font-medium block mb-1.5">Expires</label>
        <Input id={`${idPrefix}-until`} type="date" min={dayInput(1)} max={latestExpiryDay()} value={until} onChange={(e) => setUntil(e.target.value)} />
      </div>
      <div className="flex items-end gap-2">
        <Button type="submit" disabled={!canSubmit}>
          {save.isPending ? (editing ? "Saving…" : "Adding…") : editing ? "Save waiver" : "Add waiver"}
        </Button>
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
      {isError && (
        <p role="alert" className={compact ? "text-xs text-destructive" : "sm:col-span-2 text-xs text-destructive"}>
          Couldn&apos;t load the list of checks that can be waived. Reload the page to try again.
        </p>
      )}
      {save.isError && (
        <p role="alert" className={compact ? "text-xs text-destructive" : "sm:col-span-2 text-xs text-destructive"}>
          Couldn&apos;t {editing ? "save" : "add"} the waiver: {save.error.message}
        </p>
      )}
    </form>
  )
}
