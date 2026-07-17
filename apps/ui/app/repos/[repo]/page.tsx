"use client"

// URL: /repos/<owner~name>. The [repo] folder name is Next.js dynamic segment syntax
// (not a literal path); param holds owner and repo joined with "~" (see repos/page.tsx).

import { useEffect, useState } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import { useMutation, useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Skeleton } from "@/components/ui/skeleton"
import { GitPullRequest, ArrowSquareOut } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { parseOwnerRepo } from "@/lib/repo-segment"
import { shouldApplyResolvedToken } from "@/lib/token-resolve"
import { AreaTimeChart } from "@/components/charts/area-time-chart"
import { BarGroupChart } from "@/components/charts/bar-group-chart"
import { HeatmapCalendar } from "@/components/charts/heatmap-calendar"
import { CHART_COLORS } from "@/lib/charts/theme"
import { CachePanel } from "@/components/repo/cache-panel"

const HEATMAP_SCALE = [CHART_COLORS.grid, "#1e3a8a", "#1d4ed8", "#3b82f6", "#60a5fa"]

type Tab = "overview" | "cache" | "security"

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "cache", label: "Actions Cache" },
  { id: "security", label: "Security" },
]

export default function RepoDetailPage() {
  const params = useParams<{ repo: string }>()
  const parsed = parseOwnerRepo(params.repo || "")
  const owner = parsed?.owner ?? ""
  const repo = parsed?.repo ?? ""

  const [tab, setTab] = useState<Tab>("overview")
  const [token, setToken] = useState("")

  const resolveMutation = useMutation({
    mutationFn: (org: string) => api.tokens.resolve(org),
    onSuccess: (data, org) => {
      if (shouldApplyResolvedToken(org, owner)) setToken(data.token)
    },
  })

  useEffect(() => {
    if (owner) {
      setToken("")
      resolveMutation.mutate(owner)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner])

  useEffect(() => {
    setTab("overview")
  }, [params.repo])

  const statsQuery = useQuery({
    queryKey: ["repo-detail-stats", owner, repo, token],
    queryFn: () => api.repos.stats(owner, owner, repo, token),
    enabled: !!owner && !!repo,
  })

  const pullsQuery = useQuery({
    queryKey: ["repo-detail-pulls", owner, repo, token],
    queryFn: () => api.repos.pulls(owner, owner, repo, token),
    enabled: !!owner && !!repo,
  })

  // Org-wide security scan is a heavier call (every check across every repo in the org,
  // not just this one) — only fire it once the Security tab is actually opened, not on
  // every repo detail page visit.
  const securityQuery = useQuery({
    queryKey: ["repo-detail-security", owner, token],
    queryFn: () => api.analytics.overview(owner, token),
    enabled: tab === "security" && !!owner,
  })

  if (!parsed) {
    return (
      <>
        <PageHeader title="Repository" description="Invalid repository route." />
        <div className="bg-card border border-border px-4 py-6 text-sm text-muted-foreground">
          Expected URL format: <span className="font-mono">/repos/owner~repo</span>
        </div>
      </>
    )
  }

  const commitActivity = statsQuery.data?.commit_activity ?? []
  const areaData = commitActivity.map((w) => ({
    week: new Date(w.week * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }),
    value: w.total,
  }))
  const heatmapData = commitActivity.map((w) => w.total)
  const topContributors = [...(statsQuery.data?.contributors ?? [])]
    .sort((a, b) => b.total - a.total)
    .slice(0, 8)
    .map((c) => ({ name: c.login ?? "unknown", commits: c.total }))

  return (
    <>
      <PageHeader
        title={repo}
        description={`${owner}/${repo}`}
        actions={
          <span className="inline-flex items-center gap-1.5 stat-chip">
            <GitPullRequest className="size-3.5" />
            {pullsQuery.isLoading ? "…" : (pullsQuery.data?.total ?? 0)} open PRs
          </span>
        }
      />

      <div className="flex items-center gap-1 border-b border-border mb-4" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
              tab === t.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="bg-card border border-border lg:col-span-2">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Commit activity — 52 weeks</span>
            </div>
            <div className="p-4">
              {statsQuery.isLoading ? (
                <Skeleton className="h-60 w-full" />
              ) : areaData.length === 0 ? (
                <p className="text-sm text-muted-foreground font-mono py-8 text-center">
                  — no commit activity available yet
                </p>
              ) : (
                <AreaTimeChart data={areaData} label="commits" height={240} />
              )}
            </div>
          </div>

          <div className="bg-card border border-border">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Activity calendar</span>
            </div>
            <div className="p-4">
              {statsQuery.isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : heatmapData.length === 0 ? (
                <p className="text-sm text-muted-foreground font-mono">— no data yet</p>
              ) : (
                <HeatmapCalendar data={heatmapData} colorScale={HEATMAP_SCALE} />
              )}
            </div>
          </div>

          <div className="bg-card border border-border">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Top contributors</span>
            </div>
            <div className="p-4">
              {statsQuery.isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : topContributors.length === 0 ? (
                <p className="text-sm text-muted-foreground font-mono">— no contributor data yet</p>
              ) : (
                <BarGroupChart
                  data={topContributors}
                  bars={[{ key: "commits", color: CHART_COLORS.primary }]}
                  height={180}
                />
              )}
            </div>
          </div>

          {statsQuery.isError && (
            <p className="text-xs text-destructive lg:col-span-2">{statsQuery.error.message}</p>
          )}
        </div>
      )}

      {tab === "cache" && <CachePanel owner={owner} repo={repo} />}

      {tab === "security" && (
        <div className="bg-card border border-border">
          <div className="px-4 py-3 border-b border-border flex items-center justify-between">
            <span className="section-label">Organization security score</span>
            <Link
              href="/security"
              onClick={() => localStorage.setItem("default_org", owner)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors inline-flex items-center gap-1"
            >
              Full report <ArrowSquareOut className="size-3" />
            </Link>
          </div>
          <div className="p-4">
            <p className="text-xs text-muted-foreground mb-3">
              This score covers every repository in <span className="font-mono">{owner}</span>, not just{" "}
              <span className="font-mono">{repo}</span> — there is no per-repo security scan yet.
            </p>
            {securityQuery.isLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : securityQuery.isError ? (
              <p className="text-xs text-destructive">{securityQuery.error.message}</p>
            ) : securityQuery.data ? (
              <div className="flex items-center gap-4">
                <span className="text-2xl font-bold tabular-nums text-foreground">{securityQuery.data.score}</span>
                <div className="flex items-center gap-2">
                  <span className="stat-chip">
                    {securityQuery.data.total_checks - securityQuery.data.failed_checks} passed
                  </span>
                  {securityQuery.data.failed_checks > 0 && (
                    <span className="stat-chip text-red-400 border-red-500/30">
                      {securityQuery.data.failed_checks} failed
                    </span>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </>
  )
}
