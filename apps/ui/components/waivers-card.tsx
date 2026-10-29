"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { dueTime } from "@/lib/format"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { CheckWaiver } from "@/lib/api/types"

/** Checks an admin can waive: id -> title. Mirrors the API's known checks; an unknown id is refused there. */
export const WAIVABLE_CHECKS: Record<string, string> = {
  organization_members_mfa_required: "Organization requires 2FA/MFA",
  repository_default_branch_protection_enabled: "Default branch has protection rules",
  repository_secret_scanning_enabled: "Secret scanning enabled",
  repository_dependabot_alerts_clear: "No open critical/high Dependabot alerts",
  repository_code_scanning_alerts_clear: "No open code scanning alerts",
  repository_default_branch_no_force_push: "Default branch disallows force pushes",
  repository_codeowners_present: "CODEOWNERS present",
  repository_security_policy_present: "SECURITY.md present",
  repository_license_present: "License present",
  repository_no_stale_branches: "No stale branches",
  repository_actions_pinned_to_sha: "Actions pinned to a commit SHA",
}

const MAX_DAYS = 90
const dayInput = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10)

/** Org admins record accepted risks: a failing check is kept out of the score until the waiver expires. */
export function WaiversCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["waivers", orgLogin]
  const query = useQuery<CheckWaiver[]>({ queryKey, queryFn: () => api.waivers.list(orgLogin), retry: false })

  const [checkId, setCheckId] = useState(Object.keys(WAIVABLE_CHECKS)[1])
  const [repos, setRepos] = useState("")
  const [reason, setReason] = useState("")
  const [owner, setOwner] = useState("")
  const [until, setUntil] = useState(dayInput(30))

  const create = useMutation({
    mutationFn: () =>
      api.waivers.create(orgLogin, {
        check_id: checkId,
        repos: repos.split(",").map((r) => r.trim()).filter(Boolean),
        reason,
        owner,
        // End of the chosen day, local time.
        expires_at: new Date(`${until}T23:59:59`).toISOString(),
      }),
    onSuccess: () => {
      setReason("")
      setRepos("")
      return queryClient.invalidateQueries({ queryKey })
    },
  })
  const revoke = useMutation({
    mutationFn: (id: number) => api.waivers.revoke(orgLogin, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  })

  const canSubmit = reason.trim() !== "" && owner.trim() !== "" && until !== "" && !create.isPending

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Check waivers</span>
      </div>
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Record an accepted risk: the failing check stays visible but is left out of the score until the waiver expires
        (at most {MAX_DAYS} days). Every change is written to the audit log.
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
          {query.data.map((w) => (
            <li key={w.id} className="px-4 py-2 flex items-start justify-between gap-3 text-xs">
              <div className="min-w-0">
                <p className="font-medium text-foreground">
                  {WAIVABLE_CHECKS[w.check_id] ?? w.check_id}
                  <span className="ml-2 text-muted-foreground font-normal">{w.repos ? w.repos.join(", ") : "whole organization"}</span>
                </p>
                <p className="text-muted-foreground">
                  {w.reason} · owner {w.owner} ·{" "}
                  {w.active ? `expires ${dueTime(w.expires_at)}` : <span className="text-destructive">expired {dueTime(w.expires_at)}</span>}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(w.id)}
                aria-label={`Revoke waiver for ${WAIVABLE_CHECKS[w.check_id] ?? w.check_id}`}
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      )}
      {revoke.isError && (
        <p role="alert" className="px-4 pb-2 text-xs text-destructive">
          Couldn&apos;t revoke: {revoke.error.message}
        </p>
      )}

      <form
        className="border-t border-border p-4 grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) create.mutate()
        }}
      >
        <div>
          <label htmlFor="waiver-check" className="text-xs font-medium block mb-1.5">Check</label>
          <select
            id="waiver-check"
            value={checkId}
            onChange={(e) => setCheckId(e.target.value)}
            className="text-xs card text-foreground px-2 py-1.5 w-full focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {Object.entries(WAIVABLE_CHECKS).map(([id, title]) => (
              <option key={id} value={id}>{title}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="waiver-repos" className="text-xs font-medium block mb-1.5">Repositories (optional)</label>
          <Input id="waiver-repos" placeholder="legacy-api, old-site — blank = whole organization" value={repos} onChange={(e) => setRepos(e.target.value)} />
        </div>
        <div>
          <label htmlFor="waiver-reason" className="text-xs font-medium block mb-1.5">Reason</label>
          <Input id="waiver-reason" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <div>
          <label htmlFor="waiver-owner" className="text-xs font-medium block mb-1.5">Owner</label>
          <Input id="waiver-owner" placeholder="person or team accountable" maxLength={100} value={owner} onChange={(e) => setOwner(e.target.value)} />
        </div>
        <div>
          <label htmlFor="waiver-until" className="text-xs font-medium block mb-1.5">Expires</label>
          <Input id="waiver-until" type="date" min={dayInput(1)} max={dayInput(MAX_DAYS - 1)} value={until} onChange={(e) => setUntil(e.target.value)} />
        </div>
        <div className="flex items-end">
          <Button type="submit" disabled={!canSubmit}>
            {create.isPending ? "Adding…" : "Add waiver"}
          </Button>
        </div>
        {create.isError && (
          <p role="alert" className="sm:col-span-2 text-xs text-destructive">
            Couldn&apos;t add the waiver: {create.error.message}
          </p>
        )}
      </form>
    </div>
  )
}
