"use client"

import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import Link from "next/link"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { toast } from "@/components/ui/toast"
import { Warning, Eye, Key, CircleNotch, Trash } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"
import { shouldApplyResolvedToken } from "@/lib/token-resolve"
import { BarGroupChart } from "@/components/charts/bar-group-chart"
import { CHART_COLORS } from "@/lib/charts/theme"
import { formatBytes, relativeTime, classifyStaleness, stalenessColor } from "@/lib/format"
import type { CacheEntry, InstallationMeta, JobOut } from "@/lib/api/types"

interface CachePanelProps {
  owner: string
  repo: string
  // The repo detail page keeps this panel mounted behind all tabs, so this defers token
  // resolution until the tab is opened. Defaults to true for the standalone /cache route.
  active?: boolean
}

/** Actions-cache list/clear UI for the /repos/{repo}/cache route and the repo detail tab. */
export function CachePanel({ owner, repo, active = true }: CachePanelProps) {
  const { user } = useAuth()
  const [token, setToken] = useState("")
  const [tokenSaved, setTokenSaved] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  // Enqueued job id after a real clear, polled so we report the actual outcome.
  const [jobId, setJobId] = useState<number | null>(null)
  // null = clear every cache for this repo; { key, ref } = a single row's "Clear".
  const [clearTarget, setClearTarget] = useState<{ key: string; ref: string } | null>(null)

  const { data: installs = [] } = useQuery<InstallationMeta[]>({
    queryKey: ["installations"],
    queryFn: () => api.installations.list(),
  })
  // list() only covers personal installations; orgs need the org-scoped endpoint. Errors
  // (403/404) are treated as "not installed" since this only decides whether to hide the token field.
  const orgInstallsQuery = useQuery<InstallationMeta[]>({
    queryKey: ["installations.org", owner],
    queryFn: () => api.installations.listForOrg(owner),
    enabled: !!owner,
    retry: false,
  })
  const hasInstallationForOwner =
    installs.some((i) => i.account_login === owner) || (orgInstallsQuery.data?.length ?? 0) > 0

  const resolveMutation = useMutation({
    mutationFn: (org: string) => api.tokens.resolve(org),
    onSuccess: (data, org) => {
      // Skip a legacy saved token once an installation covers this owner: the token field is
      // hidden then, and the saved token would silently override the installation-token path.
      if (shouldApplyResolvedToken(org, owner) && !hasInstallationForOwner) {
        setToken(data.token)
        setTokenSaved(true)
      }
    },
    onError: () => setTokenSaved(false),
  })

  // Resolve at most once per owner, deferred until the panel is active, but not on every
  // tab revisit, which would wipe out whatever the user typed meanwhile.
  const resolvedForOwnerRef = useRef(false)

  useEffect(() => {
    resolvedForOwnerRef.current = false
    if (owner) {
      setToken("")
      setTokenSaved(false)
    }
  }, [owner])

  useEffect(() => {
    if (owner && active && !resolvedForOwnerRef.current) {
      resolvedForOwnerRef.current = true
      resolveMutation.mutate(owner)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, active])

  // Reset stale table/result data and any open confirm dialog when owner/repo changes.
  useEffect(() => {
    listMutation.reset()
    clearMutation.reset()
    setConfirmOpen(false)
    setClearTarget(null)
    setJobId(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, repo])


  const saveTokenMutation = useMutation({
    mutationFn: () => api.tokens.upsert(owner, token.trim()),
    onSuccess: () => setTokenSaved(true),
  })

  const listMutation = useMutation({
    mutationFn: () => api.cache.list(owner, repo, token),
  })

  const clearMutation = useMutation({
    mutationFn: (dryRun: boolean) =>
      api.cache.clear(owner, repo, {
        token,
        dry_run: dryRun,
        key: clearTarget?.key,
        ref: clearTarget?.ref,
      }),
    onSuccess: (data) => {
      setConfirmOpen(false)
      if (data.dry_run) {
        toast.info("Dry run complete — no caches were deleted.")
      } else if (data.job_id) {
        setJobId(data.job_id)
        toast.info(`Cache clear queued — Job #${data.job_id}`)
      }
    },
    onError: (error) => {
      setConfirmOpen(false)
      toast.error(error.message)
    },
  })

  // Poll the enqueued job until terminal: the clear runs in the worker, not in this request.
  const jobQuery = useQuery<JobOut>({
    queryKey: ["job", jobId],
    queryFn: () => api.jobs.get(jobId as number),
    enabled: jobId != null,
    refetchInterval: (query) => {
      // Also stop once the status request exhausts its retries, or a failing GET polls forever.
      if (query.state.status === "error") return false
      const status = query.state.data?.status
      return status === "done" || status === "failed" ? false : 2000
    },
  })

  const job = jobId != null ? jobQuery.data : undefined
  const clearedCount = (() => {
    if (job?.status !== "done" || !job.result) return null
    try {
      const parsed = JSON.parse(job.result) as { deleted?: number }
      return typeof parsed.deleted === "number" ? parsed.deleted : null
    } catch {
      return null
    }
  })()

  useEffect(() => {
    if (job?.status === "done") {
      // The clear ran in the worker; refresh the table so it stops showing deleted caches.
      if (listMutation.data) listMutation.mutate()
      toast.success(
        clearedCount != null
          ? `Cache cleared — ${clearedCount} ${clearedCount === 1 ? "entry" : "entries"} deleted.`
          : "Cache cleared.",
      )
    } else if (job?.status === "failed") {
      toast.error(`Cache clear failed — ${job.result ?? "unknown error"}`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status])

  // A queued/processing clear job blocks another clear until it finishes.
  const jobActive = jobId != null && job?.status !== "done" && job?.status !== "failed" && !jobQuery.isError
  const isLoading = listMutation.isPending || clearMutation.isPending || jobActive
  const caches: CacheEntry[] = listMutation.data?.actions_caches ?? []
  const totalBytes = caches.reduce((sum, c) => sum + c.size_in_bytes, 0)

  const cacheByRef = caches.reduce<Record<string, number>>((acc, c) => {
    acc[c.ref] = (acc[c.ref] ?? 0) + c.size_in_bytes
    return acc
  }, {})
  const cacheChartData = Object.entries(cacheByRef).map(([ref, bytes]) => ({
    name: ref,
    mb: Math.round((bytes / 1_048_576) * 100) / 100,
  }))

  return (
    <>
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="card">
        <div className="px-4 py-3 border-b border-border">
          <span className="section-label">Configuration</span>
        </div>
        <div className="p-4 flex flex-col gap-3">
          {!hasInstallationForOwner && (
            <div>
              <label className="text-xs font-medium text-foreground mb-1.5 flex items-center gap-1.5">
                GitHub Token
                <span className="text-[0.6875rem] text-muted-foreground font-normal">
                  optional if the GitHub App is connected for this org
                </span>
                {tokenSaved && (
                  <span className="inline-flex items-center gap-1 text-[0.6875rem] text-primary">
                    <Key className="size-3" />saved
                  </span>
                )}
              </label>
              <Input
                placeholder="ghp_... (leave blank to use the connected GitHub App)"
                type="password"
                value={token}
                onChange={(e) => { setToken(e.target.value); setTokenSaved(false) }}
                className="font-mono"
              />
            </div>
          )}
          {!tokenSaved && token && (
            <Button
              variant="outline"
              onClick={() => saveTokenMutation.mutate()}
              disabled={saveTokenMutation.isPending}
              className="w-full"
            >
              <Key className="size-3.5" />
              {saveTokenMutation.isPending ? "Saving…" : "Save token for this org"}
            </Button>
          )}
          {saveTokenMutation.isError && (
            <p className="text-xs text-destructive flex items-center gap-1.5">
              <Warning className="size-3 shrink-0" />
              {saveTokenMutation.error.message}
            </p>
          )}
          <Button
            onClick={() => listMutation.mutate()}
            disabled={isLoading}
            className="mt-1"
          >
            {listMutation.isPending ? (
              <><CircleNotch className="size-3.5 animate-spin" />Loading…</>
            ) : (
              "Load caches"
            )}
          </Button>
          {listMutation.isError && (
            <p className="text-xs text-destructive flex items-center gap-1.5">
              <Warning className="size-3 shrink-0" />
              {listMutation.error.message}
            </p>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Button
              variant="outline"
              onClick={() => {
                setClearTarget(null)
                clearMutation.mutate(true)
                // The preview lists what a clear would delete, so fetch the current caches.
                listMutation.mutate()
              }}
              disabled={isLoading}
            >
              <Eye className="size-3.5" />
              Dry run
            </Button>
            <Button
              variant="destructive"
              onClick={() => { setClearTarget(null); setConfirmOpen(true) }}
              disabled={isLoading}
            >
              <Trash className="size-3.5" />
              Clear
            </Button>
          </div>
          {clearMutation.isError && (
            <p className="text-xs text-destructive flex items-center gap-1.5">
              <Warning className="size-3 shrink-0" />
              {clearMutation.error.message}
            </p>
          )}
        </div>
      </div>

      <div className="card lg:col-span-2">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="section-label">Cache entries</span>
          <div className="flex items-center gap-2">
            {caches.length > 0 && (
              <>
                <span className="stat-chip">{formatBytes(totalBytes)}</span>
                <span className="stat-chip">{caches.length} total</span>
              </>
            )}
          </div>
        </div>

        {caches.length > 0 && (
          <div className="p-4 border-b border-border">
            <p className="section-label mb-3">MB cached by ref</p>
            <BarGroupChart
              data={cacheChartData}
              bars={[{ key: "mb", color: CHART_COLORS.primary }]}
              height={180}
            />
          </div>
        )}

        {listMutation.isPending ? (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Key</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Ref</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Size</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Created</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Last accessed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i}>
                    <td className="px-4 py-3"><Skeleton className="h-3 w-36" /></td>
                    <td className="px-4 py-3"><Skeleton className="h-3 w-20" /></td>
                    <td className="px-4 py-3 text-right"><Skeleton className="h-3 w-12 ml-auto" /></td>
                    <td className="px-4 py-3 text-right"><Skeleton className="h-3 w-16 ml-auto" /></td>
                    <td className="px-4 py-3 text-right"><Skeleton className="h-3 w-20 ml-auto" /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : caches.length === 0 ? (
          <div className="px-4 py-8">
            <p className="text-sm text-muted-foreground font-mono">
              — click &ldquo;Load caches&rdquo; to list entries
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Key</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Ref</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Size</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Created</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Last accessed</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {caches.map((c) => {
                  const staleness = classifyStaleness(c.last_accessed_at)
                  const { text: staleText, dot: staleDot } = stalenessColor[staleness]
                  return (
                    <tr key={c.id} className="hover:bg-muted/40 transition-colors">
                      <td className="px-4 py-2.5 font-mono text-foreground/80 max-w-[14rem] truncate">{c.key}</td>
                      <td className="px-4 py-2.5 text-muted-foreground max-w-[8rem] truncate">{c.ref}</td>
                      <td className="px-4 py-2.5 text-right font-mono text-muted-foreground tabular-nums">
                        {formatBytes(c.size_in_bytes)}
                      </td>
                      <td className="px-4 py-2.5 text-right text-muted-foreground whitespace-nowrap">
                        {relativeTime(c.created_at)}
                      </td>
                      <td className="px-4 py-2.5 text-right whitespace-nowrap">
                        <span className={`inline-flex items-center gap-1 font-mono text-[0.6875rem] ${staleText}`}>
                          <span className={`inline-block size-1.5 rounded-full ${staleDot}`} />
                          {relativeTime(c.last_accessed_at)}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Button
                          variant="outline"
                          className="h-6 px-2 text-[0.6875rem]"
                          disabled={isLoading}
                          onClick={() => { setClearTarget({ key: c.key, ref: c.ref }); setConfirmOpen(true) }}
                        >
                          <Trash className="size-3" />
                          Clear key
                        </Button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>

    {(clearMutation.data?.dry_run || jobId != null) && (
      <div className="card mt-4">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="section-label">Result</span>
          {clearMutation.data?.dry_run && (
            <span className="stat-chip text-yellow-400 border-yellow-500/30">dry run</span>
          )}
        </div>
        <div className="p-4">
          {clearMutation.data?.dry_run ? (
            <p className="text-sm text-yellow-400/80">
              {listMutation.isPending
                ? "Dry run complete — loading the caches a clear would delete…"
                : listMutation.isSuccess
                  ? `Dry run complete — a clear would delete ${caches.length} ${caches.length === 1 ? "cache" : "caches"} (${formatBytes(totalBytes)}). Nothing was deleted.`
                  : "Dry run complete — no caches were deleted."}
            </p>
          ) : (
            <div className="flex items-center gap-3">
              {job?.status === "done" ? (
                <p className="text-sm text-green-400">
                  {clearedCount != null
                    ? `Cache cleared — ${clearedCount} ${clearedCount === 1 ? "entry" : "entries"} deleted.`
                    : "Cache cleared."}
                </p>
              ) : job?.status === "failed" ? (
                <p className="text-sm text-destructive">
                  Cache clear failed — {job.result ?? "unknown error"}
                </p>
              ) : jobQuery.error ? (
                <div className="text-sm text-destructive flex items-center gap-2">
                  Couldn&apos;t check job status — {jobQuery.error.message}
                  <Button
                    variant="outline"
                    className="h-6 px-2 text-[0.6875rem]"
                    onClick={() => jobQuery.refetch()}
                  >
                    Retry
                  </Button>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground flex items-center gap-2">
                  <CircleNotch className="size-3.5 animate-spin" />
                  {job?.status === "processing" ? "Clearing caches…" : "Queued…"} (Job #{jobId})
                </p>
              )}
              {user?.is_workspace_admin && <Link
                href={`/audit?job_id=${jobId}`}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap"
              >
                View in Audit Log →
              </Link>}
            </div>
          )}
        </div>
      </div>
    )}

    <ConfirmDialog
      open={confirmOpen}
      onOpenChange={setConfirmOpen}
      title="Delete cache?"
      description={
        clearTarget
          ? `This permanently deletes the "${clearTarget.key}" cache entry (ref ${clearTarget.ref}) — this can't be undone.`
          : "This permanently deletes every Actions cache entry for this repo — this can't be undone."
      }
      confirmLabel="Delete"
      onConfirm={() => clearMutation.mutate(false)}
      pending={clearMutation.isPending}
    />
    </>
  )
}
