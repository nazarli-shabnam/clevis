"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Eye, Trash } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/toast"
import { SectionError } from "@/components/section-error"
import { EmptyStateNoAccount } from "@/components/empty-state"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import { isOrgMemberOnly } from "@/lib/members-href"
import { formatBytes, relativeTime } from "@/lib/format"
import { rankByCacheSize, summarizeCaches, type RepoCacheStats } from "@/lib/cache-rank"
import type { JobOut, MyOrgMembership } from "@/lib/api/types"

// Requests are fanned out in batches (same as the Pull Requests page) so a large org doesn't fire
// dozens of simultaneous GitHub calls.
const REPO_BATCH_SIZE = 10

const isTerminal = (job: JobOut) => job.status === "done" || job.status === "failed"

async function inBatches<T>(items: string[], run: (item: string) => Promise<T>): Promise<T[]> {
  const out: T[] = []
  for (let i = 0; i < items.length; i += REPO_BATCH_SIZE) {
    out.push(...(await Promise.all(items.slice(i, i + REPO_BATCH_SIZE).map(run))))
  }
  return out
}

export default function CachesPage() {
  const { scope } = useActiveScope()
  const org = scope?.kind === "org" ? scope.login : ""
  const hasOrg = org.trim().length > 0

  const [staleOnly, setStaleOnly] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)
  // Jobs enqueued by the last real clear, polled until every one finishes.
  const [jobIds, setJobIds] = useState<number[]>([])

  // Clearing deletes caches through the repo clear route, which needs org admin; a plain member would 403.
  const { data: memberships } = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })
  // Unknown until memberships load: don't offer the controls to someone who may turn out to be a plain member.
  const canClear = !!memberships && !isOrgMemberOnly(memberships, org)

  // A selection and an open confirmation belong to one org; never carry them over to another.
  useEffect(() => {
    setSelected(new Set())
    setConfirmOpen(false)
  }, [org])

  // The API resolves the installation token (or a workspace admin's saved PAT) server-side.
  const token = ""
  const queriesEnabled = hasOrg

  const reposQuery = useQuery({
    queryKey: ["repos.list", org],
    queryFn: () => api.repos.list(org, token),
    enabled: queriesEnabled,
    retry: false,
  })
  const repoNames = reposQuery.data?.repos.map((r) => r.name) ?? []

  const cachesQuery = useQuery({
    queryKey: ["repos.caches.all", org, repoNames.join(",")],
    queryFn: async () => {
      // A repo whose request fails must not look like a repo with no cache.
      const failedRepos: string[] = []
      let firstError = ""
      const rows = await inBatches(repoNames, (repo) =>
        api.cache
          .list(org, repo, token)
          .then((r) => summarizeCaches(repo, r.actions_caches))
          .catch((e: unknown) => {
            failedRepos.push(repo)
            if (!firstError && e instanceof Error) firstError = e.message
            return null
          }),
      )
      if (repoNames.length > 0 && failedRepos.length === repoNames.length) {
        throw new Error(firstError || "Failed to load Actions caches.")
      }
      return { rows: rows.filter((r): r is RepoCacheStats => r !== null), failedRepos }
    },
    enabled: queriesEnabled && !!reposQuery.data,
    retry: false,
  })

  const all = cachesQuery.data?.rows ?? []
  const failedRepos = cachesQuery.data?.failedRepos ?? []
  const ranked = rankByCacheSize(all, staleOnly)
  const isLoading = reposQuery.isLoading || (reposQuery.isSuccess && cachesQuery.isLoading)
  const totalBytes = all.reduce((sum, r) => sum + r.totalBytes, 0)
  const staleBytes = all.reduce((sum, r) => sum + r.staleBytes, 0)

  // Selection only ever holds repos still on screen, so "Clear selected" can't touch a hidden row.
  const visible = new Set(ranked.map((r) => r.repo))
  const chosen = ranked.filter((r) => selected.has(r.repo))
  const allSelected = ranked.length > 0 && chosen.length === ranked.length

  function toggle(repo: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(repo)) next.delete(repo)
      else next.add(repo)
      return next
    })
  }

  const clearMutation = useMutation({
    mutationFn: async ({ repos, dryRun }: { repos: string[]; dryRun: boolean; rows: RepoCacheStats[] }) => {
      const results = await inBatches(repos, (repo) =>
        api.cache
          .clear(org, repo, { token, dry_run: dryRun })
          .then((r) => ({ repo, jobId: r.job_id ?? null, ok: true as const }))
          .catch((e: unknown) => ({ repo, jobId: null, ok: false as const, error: e instanceof Error ? e.message : "" })),
      )
      return { dryRun, results }
    },
    onSuccess: ({ dryRun, results }, { rows }) => {
      setConfirmOpen(false)
      const failed = results.filter((r) => !r.ok)
      const okCount = results.length - failed.length
      const failNote = failed.length > 0 ? ` ${failed.length} failed (${failed.map((f) => f.repo).join(", ")}).` : ""
      if (dryRun) {
        // The rows that were selected when the request was sent, and only those whose request succeeded.
        const succeeded = new Set(results.filter((r) => r.ok).map((r) => r.repo))
        const bytes = rows.filter((r) => succeeded.has(r.repo)).reduce((sum, r) => sum + r.totalBytes, 0)
        toast.info(`Dry run complete for ${okCount} repo${okCount === 1 ? "" : "s"} (${formatBytes(bytes)} would be deleted). Nothing was changed.${failNote}`)
      } else {
        const ids = results.flatMap((r) => (r.jobId != null ? [r.jobId] : []))
        setJobIds(ids)
        toast.info(`Cache clear queued for ${ids.length} repo${ids.length === 1 ? "" : "s"}.${failNote}`)
      }
    },
    onError: (e) => {
      setConfirmOpen(false)
      toast.error(e instanceof Error ? e.message : "Cache clear failed.")
    },
  })

  // The clears run in the worker, not in these requests, so poll every job until it is terminal.
  const jobsQuery = useQuery<JobOut[]>({
    queryKey: ["cache-clear-jobs", jobIds],
    queryFn: () => Promise.all(jobIds.map((id) => api.jobs.get(id))),
    enabled: jobIds.length > 0,
    refetchInterval: (query) => {
      // Also stop once the status request exhausts its retries, or a failing GET polls forever.
      if (query.state.status === "error") return false
      const jobs = query.state.data
      return jobs && jobs.length === jobIds.length && jobs.every(isTerminal) ? false : 2000
    },
  })
  const jobs = jobsQuery.data ?? []
  const settled = jobIds.length > 0 && jobs.length === jobIds.length && jobs.every(isTerminal)
  const clearing = jobIds.length > 0 && !settled && !jobsQuery.isError

  useEffect(() => {
    if (!settled) return
    const failed = jobs.filter((j) => j.status === "failed").length
    if (failed > 0) toast.error(`${failed} of ${jobs.length} cache clears failed. Check the Audit Log for details.`)
    else toast.success(`Cache cleared for ${jobs.length} repo${jobs.length === 1 ? "" : "s"}.`)
    setJobIds([])
    setSelected(new Set())
    cachesQuery.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled])

  const busy = clearMutation.isPending || clearing

  return (
    <>
      <PageHeader title="Actions Cache" description="Which repositories hold the most Actions cache, and clearing it in bulk." />
      <div className="card">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
          <span className="section-title">Cache by repository</span>
          <div className="flex items-center gap-3 flex-wrap">
            {all.length > 0 && (
              <>
                <span className="stat-chip">{formatBytes(totalBytes)} total</span>
                <span className="stat-chip">{formatBytes(staleBytes)} stale</span>
              </>
            )}
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={staleOnly} onChange={(e) => setStaleOnly(e.target.checked)} />
              Stale only (not accessed in 7+ days)
            </label>
            {canClear && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={chosen.length === 0 || busy}
                  onClick={() => clearMutation.mutate({ repos: chosen.map((r) => r.repo), dryRun: true, rows: chosen })}
                >
                  <Eye className="size-3.5" /> Dry run ({chosen.length})
                </Button>
                <Button size="sm" variant="destructive" disabled={chosen.length === 0 || busy} onClick={() => setConfirmOpen(true)}>
                  <Trash className="size-3.5" /> {clearing ? "Clearing…" : `Clear selected (${chosen.length})`}
                </Button>
              </>
            )}
          </div>
        </div>
        {failedRepos.length > 0 && !cachesQuery.isError && (
          <p role="alert" className="px-4 py-2 text-xs text-destructive border-b border-border">
            {failedRepos.length} repositor{failedRepos.length === 1 ? "y" : "ies"} could not be loaded
            ({failedRepos.slice(0, 5).join(", ")}{failedRepos.length > 5 ? ", …" : ""}), so this list may be incomplete.{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => cachesQuery.refetch()} disabled={cachesQuery.isFetching}>
              Retry
            </button>
          </p>
        )}
        {!hasOrg ? (
          <EmptyStateNoAccount bare message={scope?.kind === "personal" ? "This view lists organization repositories. Pick an organization from the profile menu." : undefined} />
        ) : reposQuery.isError ? (
          <SectionError
            message={reposQuery.error instanceof Error ? reposQuery.error.message : "Failed to load repositories."}
            onRetry={() => reposQuery.refetch()}
            retrying={reposQuery.isFetching}
          />
        ) : cachesQuery.isError ? (
          <SectionError
            message={cachesQuery.error instanceof Error ? cachesQuery.error.message : "Failed to load Actions caches."}
            onRetry={() => cachesQuery.refetch()}
            retrying={cachesQuery.isFetching}
          />
        ) : isLoading ? (
          <div className="p-4 flex flex-col gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : ranked.length === 0 ? (
          <p className="px-4 py-8 text-sm text-muted-foreground">
            {staleOnly ? "No repository has stale cache" : failedRepos.length > 0 ? "No Actions cache in the repositories that loaded" : "No repository has any Actions cache"}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  {canClear && (
                    <th className="pl-4 py-2 w-6">
                      <input
                        type="checkbox"
                        aria-label="Select all repositories"
                        checked={allSelected}
                        disabled={busy}
                        onChange={() => setSelected(allSelected ? new Set() : new Set(visible))}
                      />
                    </th>
                  )}
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Repository</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Entries</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Size</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Stale</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Last accessed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {ranked.map((r) => (
                  <tr key={r.repo} className="hover:bg-elevated transition-colors">
                    {canClear && (
                      <td className="pl-4 py-2.5 w-6">
                        <input
                          type="checkbox"
                          aria-label={`Select ${r.repo}`}
                          checked={selected.has(r.repo)}
                          disabled={busy}
                          onChange={() => toggle(r.repo)}
                        />
                      </td>
                    )}
                    <td className="px-4 py-2.5 font-mono">
                      <Link href={`/repos/${encodeURIComponent(`${org}~${r.repo}`)}/cache`} className="text-primary hover:underline">
                        {r.repo}
                      </Link>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">{r.count}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{formatBytes(r.totalBytes)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">{formatBytes(r.staleBytes)}</td>
                    <td className="px-4 py-2.5 text-right text-muted-foreground whitespace-nowrap">
                      {r.lastAccessed ? relativeTime(r.lastAccessed) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={`Delete cache in ${chosen.length} repo${chosen.length === 1 ? "" : "s"}?`}
        description={`This permanently deletes every Actions cache entry (${formatBytes(chosen.reduce((s, r) => s + r.totalBytes, 0))}) in: ${chosen.map((r) => r.repo).join(", ")}. This can't be undone.`}
        confirmLabel="Delete"
        onConfirm={() => clearMutation.mutate({ repos: chosen.map((r) => r.repo), dryRun: false, rows: chosen })}
        pending={clearMutation.isPending}
      />
    </>
  )
}
