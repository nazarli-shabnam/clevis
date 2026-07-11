"use client"

// URL: /repos/<owner~name>/cache. The [repo] folder name is Next.js dynamic segment
// syntax (not a literal path); param holds owner and repo joined with "~" (see repos/page.tsx).

import { useParams } from "next/navigation"
import { useEffect, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import Link from "next/link"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { AlertTriangle, Eye, KeyRound, Loader2, Trash2 } from "lucide-react"
import { api } from "@/lib/api/client"
import { parseOwnerRepo } from "@/lib/repo-segment"
import { BarGroupChart } from "@/components/charts/bar-group-chart"
import { CHART_COLORS } from "@/lib/charts/theme"
import { formatBytes, relativeTime, classifyStaleness, stalenessColor } from "@/lib/format"
import type { CacheEntry } from "@/lib/api/types"

export default function CachePage() {
  const params = useParams<{ repo: string }>()
  const [token, setToken] = useState("")
  const [tokenSaved, setTokenSaved] = useState(false)
  const [actor, setActor] = useState("")

  const parsed = parseOwnerRepo(params.repo || "")
  const owner = parsed?.owner ?? ""
  const repo = parsed?.repo ?? ""

  // Auto-resolve saved token for this owner
  const resolveMutation = useMutation({
    mutationFn: (org: string) => api.tokens.resolve(org),
    onSuccess: (data) => { setToken(data.token); setTokenSaved(true) },
    onError: () => setTokenSaved(false),
  })

  useEffect(() => {
    if (owner) {
      setToken("")
      setTokenSaved(false)
      resolveMutation.mutate(owner)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner])

  const saveTokenMutation = useMutation({
    mutationFn: () => api.tokens.upsert(owner, token.trim()),
    onSuccess: () => setTokenSaved(true),
  })

  const listMutation = useMutation({
    mutationFn: () => api.cache.list(owner, repo, token),
  })

  const clearMutation = useMutation({
    mutationFn: (dryRun: boolean) =>
      api.cache.clear(owner, repo, { token, actor, dry_run: dryRun }),
  })

  const isLoading = listMutation.isPending || clearMutation.isPending
  const caches: CacheEntry[] = listMutation.data?.actions_caches ?? []
  const totalBytes = caches.reduce((sum, c) => sum + c.size_in_bytes, 0)

  // Total cache size per ref, in MB, for the summary bar chart above the table.
  const cacheByRef = caches.reduce<Record<string, number>>((acc, c) => {
    acc[c.ref] = (acc[c.ref] ?? 0) + c.size_in_bytes
    return acc
  }, {})
  const cacheChartData = Object.entries(cacheByRef).map(([ref, bytes]) => ({
    name: ref,
    mb: Math.round((bytes / 1_048_576) * 100) / 100,
  }))

  if (!parsed) {
    return (
      <>
        <PageHeader title="Actions Cache" description="Invalid repository route." />
        <div className="bg-card border border-border px-4 py-6 text-sm text-muted-foreground">
          Expected URL format: <span className="font-mono">/repos/owner~repo/cache</span>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader title="Actions Cache" description={`${owner}/${repo}`} />

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Config panel */}
        <div className="bg-card border border-border">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-label">Configuration</span>
          </div>
          <div className="p-4 flex flex-col gap-3">
            <div>
              <label className="text-xs font-medium text-foreground mb-1.5 flex items-center gap-1.5">
                GitHub Token
                {tokenSaved && (
                  <span className="inline-flex items-center gap-1 text-[0.6875rem] text-primary">
                    <KeyRound className="size-3" />saved
                  </span>
                )}
              </label>
              <Input
                placeholder="ghp_..."
                type="password"
                value={token}
                onChange={(e) => { setToken(e.target.value); setTokenSaved(false) }}
                className="font-mono"
              />
            </div>
            {!tokenSaved && token && (
              <Button
                variant="outline"
                onClick={() => saveTokenMutation.mutate()}
                disabled={saveTokenMutation.isPending}
                className="w-full"
              >
                <KeyRound className="size-3.5" />
                {saveTokenMutation.isPending ? "Saving…" : "Save token for this org"}
              </Button>
            )}
            {saveTokenMutation.isError && (
              <p className="text-xs text-destructive flex items-center gap-1.5">
                <AlertTriangle className="size-3 shrink-0" />
                {saveTokenMutation.error.message}
              </p>
            )}
            <div>
              <label className="text-xs font-medium text-foreground block mb-1.5">Actor</label>
              <Input
                placeholder="actor"
                value={actor}
                onChange={(e) => setActor(e.target.value)}
              />
            </div>
            <Button
              onClick={() => listMutation.mutate()}
              disabled={isLoading || !token}
              className="mt-1"
            >
              {listMutation.isPending ? (
                <><Loader2 className="size-3.5 animate-spin" />Loading…</>
              ) : (
                "Load caches"
              )}
            </Button>
            {listMutation.isError && (
              <p className="text-xs text-destructive flex items-center gap-1.5">
                <AlertTriangle className="size-3 shrink-0" />
                {listMutation.error.message}
              </p>
            )}
            <div className="grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                onClick={() => clearMutation.mutate(true)}
                disabled={isLoading || !token || !actor}
              >
                <Eye className="size-3.5" />
                Dry run
              </Button>
              <Button
                variant="destructive"
                onClick={() => clearMutation.mutate(false)}
                disabled={isLoading || !token || !actor}
              >
                <Trash2 className="size-3.5" />
                Clear
              </Button>
            </div>
            {clearMutation.isError && (
              <p className="text-xs text-destructive flex items-center gap-1.5">
                <AlertTriangle className="size-3 shrink-0" />
                {clearMutation.error.message}
              </p>
            )}
          </div>
        </div>

        {/* Cache entries table */}
        <div className="bg-card border border-border lg:col-span-2">
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
            /* Skeleton while loading */
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
                — enter a token and click &ldquo;Load caches&rdquo; to list entries
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
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Clear result card */}
      {clearMutation.data && (
        <div className="bg-card border border-border mt-4">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <span className="section-label">Result</span>
            {clearMutation.data.dry_run && (
              <span className="stat-chip text-yellow-400 border-yellow-500/30">dry run</span>
            )}
          </div>
          <div className="p-4">
            {clearMutation.data.dry_run ? (
              <p className="text-sm text-yellow-400/80">
                Dry run complete — no caches were deleted.
              </p>
            ) : clearMutation.data.job_id ? (
              <div className="flex items-center gap-3">
                <p className="text-sm text-green-400">
                  Cache clear queued — Job #{clearMutation.data.job_id}
                </p>
                <Link
                  href={`/jobs?id=${clearMutation.data.job_id}`}
                  className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  View in Job Queue →
                </Link>
              </div>
            ) : (
              <pre className="font-mono text-xs text-muted-foreground leading-relaxed overflow-auto bg-muted/30 rounded-md p-3 border border-border/50">
                {JSON.stringify(clearMutation.data, null, 2)}
              </pre>
            )}
          </div>
        </div>
      )}
    </>
  )
}
