"use client"

import { useEffect, useMemo, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { CircleNotch, Wrench } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import type { BulkRemediateItem, BulkRemediateStatus, RepoSecurityRow } from "@/lib/api/types"

interface FixableCheck {
  id: string
  label: string
  /** Repos in the matrix that fail this check. Dimensions the token couldn't evaluate are never
   * counted as failing: "unknown" is not "off". */
  failing: (row: RepoSecurityRow) => boolean
}

const unknown = (row: RepoSecurityRow, dimension: string) => row.unknown_dimensions.includes(dimension)

// Matches the API's cap: a batch runs synchronously in one request, a few GitHub calls per repo.
export const MAX_BATCH = 100

// The checks with an automated fix on the server (check_remediation.supported_check_ids).
const FIXABLE_CHECKS: FixableCheck[] = [
  {
    id: "repository_secret_scanning_enabled",
    label: "Enable secret scanning",
    failing: (r) => !r.secret_scanning && !unknown(r, "secret_scanning"),
  },
  {
    id: "repository_default_branch_protection_enabled",
    label: "Protect the default branch",
    failing: (r) => !r.branch_protection && !unknown(r, "branch_protection"),
  },
  {
    id: "repository_default_branch_no_force_push",
    label: "Block force-pushes to the default branch",
    failing: (r) => r.force_push_allowed && !unknown(r, "force_push"),
  },
]

const STATUS_LABEL: Record<BulkRemediateStatus, string> = {
  would_change: "will change",
  unchanged: "no change needed",
  applied: "applied",
  failed: "failed",
}

const STATUS_CLASS: Record<BulkRemediateStatus, string> = {
  would_change: "text-foreground",
  unchanged: "text-muted-foreground",
  applied: "text-accent",
  failed: "text-destructive",
}

function ResultList({ items }: { items: BulkRemediateItem[] }) {
  return (
    <ul className="divide-y divide-border border border-border rounded-md text-xs">
      {items.map((item) => (
        <li key={item.repo} className="px-3 py-2 flex items-start gap-3">
          <span className="font-mono text-foreground/90 shrink-0 w-40 truncate" title={item.repo}>{item.repo}</span>
          <span className={`shrink-0 font-medium ${STATUS_CLASS[item.status]}`}>{STATUS_LABEL[item.status]}</span>
          <span className="text-muted-foreground min-w-0">{item.detail}</span>
        </li>
      ))}
    </ul>
  )
}

/**
 * "Fix many repos at once" for the compliance matrix: pick a fixable check, tick the repos that
 * fail it, preview what each would get (nothing is written), then confirm to apply. Org admins
 * only; the caller decides whether to render it.
 */
export function BulkFixPanel({
  owner,
  repos,
  token,
  onApplied,
}: {
  owner: string
  repos: RepoSecurityRow[]
  token?: string
  onApplied?: () => void
}) {
  const [checkId, setCheckId] = useState(FIXABLE_CHECKS[0].id)
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [armed, setArmed] = useState(false)

  const check = FIXABLE_CHECKS.find((c) => c.id === checkId) ?? FIXABLE_CHECKS[0]
  const candidates = useMemo(() => repos.filter((r) => check.failing(r)).map((r) => r.repo), [repos, check])
  // A fresh scan can drop repos from the candidate list: never act on a repo that's no longer there.
  const chosen = useMemo(() => candidates.filter((name) => selected.has(name)), [candidates, selected])

  const preview = useMutation({
    mutationFn: () => api.security.remediateBulk(owner, { check_id: checkId, repos: chosen, dry_run: true, token }),
  })
  const apply = useMutation({
    mutationFn: (names: string[]) =>
      api.security.remediateBulk(owner, { check_id: checkId, repos: names, dry_run: false, token }),
    onSuccess: () => onApplied?.(),
  })

  // Anything that changes what would be sent invalidates the preview and the confirmation.
  useEffect(() => {
    preview.reset()
    apply.reset()
    setArmed(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkId, owner, chosen.join("\n")])

  const toApply = (preview.data?.items ?? []).filter((i) => i.status === "would_change").map((i) => i.repo)
  const busy = preview.isPending || apply.isPending

  function toggle(name: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const atLimit = chosen.length >= MAX_BATCH
  // "Select all" can't exceed the cap: it takes the first MAX_BATCH candidates.
  const selectAllTarget = candidates.slice(0, MAX_BATCH)
  const allSelected = selectAllTarget.length > 0 && selectAllTarget.every((name) => selected.has(name))

  return (
    <div className="card mt-4">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <span className="section-title">Fix many repos at once</span>
        <span className="stat-chip">org admins only</span>
      </div>
      <div className="p-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label htmlFor="bulk-fix-check" className="font-medium text-foreground">Fix</label>
          <select
            id="bulk-fix-check"
            value={checkId}
            onChange={(e) => setCheckId(e.target.value)}
            disabled={busy}
            className="card text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {FIXABLE_CHECKS.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
          <span className="text-muted-foreground">
            {candidates.length === 0
              ? "no repos in the matrix fail this check"
              : `${candidates.length} repo${candidates.length === 1 ? "" : "s"} fail${candidates.length === 1 ? "s" : ""} it`}
          </span>
        </div>

        {candidates.length > 0 && (
          <fieldset className="border border-border rounded-md px-3 py-2" disabled={busy}>
            <legend className="px-1 text-xs text-muted-foreground">Repositories</legend>
            <label className="flex items-center gap-2 text-xs text-foreground mb-1.5">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={() => setSelected(allSelected ? new Set() : new Set(selectAllTarget))}
              />
              Select all ({selectAllTarget.length})
            </label>
            {candidates.length > MAX_BATCH && (
              <p className="text-[0.6875rem] text-muted-foreground mb-1.5">
                A batch is limited to {MAX_BATCH} repositories. Fix these, re-run the scan, then repeat for the rest.
              </p>
            )}
            <div className="max-h-40 overflow-y-auto grid gap-1 sm:grid-cols-2">
              {candidates.map((name) => (
                <label key={name} className="flex items-center gap-2 text-xs font-mono text-foreground/90">
                  <input
                    type="checkbox"
                    checked={selected.has(name)}
                    // at the cap, only already-ticked repos can be changed (to untick them)
                    disabled={atLimit && !selected.has(name)}
                    onChange={() => toggle(name)}
                  />
                  <span className="truncate" title={name}>{name}</span>
                </label>
              ))}
            </div>
          </fieldset>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" disabled={busy || chosen.length === 0} onClick={() => preview.mutate()}>
            {preview.isPending ? (
              <><CircleNotch className="size-3.5 animate-spin" /> Checking…</>
            ) : (
              <>Preview changes{chosen.length > 0 ? ` (${chosen.length})` : ""}</>
            )}
          </Button>
          {preview.data && toApply.length > 0 && !apply.isSuccess && (
            <>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => (armed ? apply.mutate(toApply) : setArmed(true))}
              >
                <Wrench className="size-3.5" />
                {apply.isPending
                  ? "Applying…"
                  : armed
                    ? `Confirm — change ${toApply.length} repo${toApply.length === 1 ? "" : "s"}`
                    : `Apply to ${toApply.length} repo${toApply.length === 1 ? "" : "s"}`}
              </Button>
              {armed && !apply.isPending && (
                <button type="button" onClick={() => setArmed(false)} className="text-xs text-muted-foreground hover:text-foreground">
                  Cancel
                </button>
              )}
            </>
          )}
        </div>

        {preview.isError && (
          <p role="alert" className="text-xs text-destructive">
            {preview.error instanceof Error ? preview.error.message : "The preview failed."}
          </p>
        )}
        {(apply.data?.hint ?? preview.data?.hint) && (
          <p role="alert" className="text-xs text-destructive">{apply.data?.hint ?? preview.data?.hint}</p>
        )}
        {preview.data && !apply.data && (
          <>
            <ResultList items={preview.data.items} />
            {toApply.length === 0 && (
              <p className="text-xs text-muted-foreground">Nothing to change for the selected repositories.</p>
            )}
          </>
        )}
        {apply.isError && (
          <p role="alert" className="text-xs text-destructive">
            {apply.error instanceof Error ? apply.error.message : "The fix could not be applied."}
          </p>
        )}
        {apply.data && (
          <>
            <ResultList items={apply.data.items} />
            <p className="text-xs text-muted-foreground">Re-run the scan to confirm the results.</p>
          </>
        )}
      </div>
    </div>
  )
}
