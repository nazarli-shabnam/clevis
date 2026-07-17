"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useMutation, useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Warning, Key, CircleNotch, Lock, Star, GitPullRequest } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { shouldApplyResolvedToken } from "@/lib/token-resolve"
import { MiniSparkline } from "@/components/charts/mini-sparkline"
import { relativeTime } from "@/lib/format"
import { useInView } from "@/lib/use-in-view"
import type { RepoSummary } from "@/lib/api/types"

function RepoActivityCell({ org, repo, token }: { org: string; repo: string; token: string }) {
  const [ref, inView] = useInView<HTMLDivElement>()
  const { data, isLoading } = useQuery({
    queryKey: ["repo-stats", org, repo, token],
    queryFn: () => api.repos.stats(org, org, repo, token),
    enabled: inView,
  })

  const weeks = (data?.commit_activity ?? []).slice(-8).map((w) => w.total)
  return (
    <div ref={ref}>
      {!inView || isLoading ? (
        <Skeleton className="h-8 w-24" />
      ) : weeks.length === 0 || weeks.every((n) => n === 0) ? (
        <span className="text-muted-foreground text-[0.6875rem]">— no recent activity</span>
      ) : (
        <MiniSparkline data={weeks} height={28} />
      )}
    </div>
  )
}

function RepoPullsCell({ org, repo, token }: { org: string; repo: string; token: string }) {
  const [ref, inView] = useInView<HTMLDivElement>()
  const { data, isLoading } = useQuery({
    queryKey: ["repo-pulls", org, repo, token],
    queryFn: () => api.repos.pulls(org, org, repo, token),
    enabled: inView,
  })

  return (
    <div ref={ref}>
      {!inView || isLoading ? (
        <Skeleton className="h-4 w-10 ml-auto" />
      ) : (
        <span className="inline-flex items-center gap-1 text-muted-foreground tabular-nums">
          <GitPullRequest className="size-3.5" />
          {data?.total ?? 0}
        </span>
      )}
    </div>
  )
}

function RepoRow({ org, repo, token }: { org: string; repo: RepoSummary; token: string }) {
  return (
    <tr className="hover:bg-muted/40 transition-colors">
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          {repo.private && <Lock className="size-3 text-muted-foreground shrink-0" />}
          <a
            href={repo.html_url}
            target="_blank"
            rel="noreferrer"
            className="font-mono text-foreground/90 hover:text-primary transition-colors truncate"
          >
            {repo.name}
          </a>
        </div>
      </td>
      <td className="px-4 py-2.5 text-muted-foreground">{repo.language ?? "—"}</td>
      <td className="px-4 py-2.5 text-right text-muted-foreground tabular-nums">
        <span className="inline-flex items-center gap-1">
          <Star className="size-3.5" />
          {repo.stargazers_count}
        </span>
      </td>
      <td className="px-4 py-2.5 text-right">
        <RepoPullsCell org={org} repo={repo.name} token={token} />
      </td>
      <td className="px-4 py-2.5 w-32">
        <RepoActivityCell org={org} repo={repo.name} token={token} />
      </td>
      <td className="px-4 py-2.5 text-right text-muted-foreground whitespace-nowrap">
        {repo.pushed_at ? relativeTime(repo.pushed_at) : "—"}
      </td>
      <td className="px-4 py-2.5 text-right whitespace-nowrap">
        <Link
          href={`/repos/${org}~${repo.name}/cache`}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          Cache →
        </Link>
      </td>
    </tr>
  )
}

export default function ReposPage() {
  const [owner, setOwner] = useState("")
  const [token, setToken] = useState("")
  const [tokenSaved, setTokenSaved] = useState(false)
  const [search, setSearch] = useState("")

  useEffect(() => {
    const defaultOrg = localStorage.getItem("default_org") || ""
    if (defaultOrg) setOwner(defaultOrg)
  }, [])

  const resolveMutation = useMutation({
    mutationFn: (org: string) => api.tokens.resolve(org),
    onSuccess: (data, org) => {
      if (shouldApplyResolvedToken(org, owner)) {
        setToken(data.token)
        setTokenSaved(true)
      }
    },
    onError: () => setTokenSaved(false),
  })

  useEffect(() => {
    setToken("")
    setTokenSaved(false)
    if (owner.trim().length > 2) resolveMutation.mutate(owner.trim())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner])

  const saveTokenMutation = useMutation({
    mutationFn: () => api.tokens.upsert(owner.trim(), token.trim()),
    onSuccess: () => setTokenSaved(true),
  })

  const [loadedToken, setLoadedToken] = useState("")

  const listMutation = useMutation({
    mutationFn: () => api.repos.list(owner.trim(), token),
    // Freeze the token used for per-row lazy fetches at load time — otherwise editing the
    // token field after a list is loaded would refetch every visible row on each keystroke.
    onSuccess: () => setLoadedToken(token),
  })

  const repos = (listMutation.data?.repos ?? []).filter((r) =>
    r.name.toLowerCase().includes(search.toLowerCase()),
  )

  return (
    <>
      <PageHeader
        title="Repositories"
        description="Browse an organization's repositories — activity, open PRs, and cache access."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Config panel */}
        <div className="bg-card border border-border">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-title">Organization</span>
          </div>
          <div className="p-4 flex flex-col gap-3">
            <div>
              <label className="text-xs font-medium text-foreground block mb-1.5">Organization</label>
              <Input
                placeholder="e.g. octocat"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && owner && !listMutation.isPending && listMutation.mutate()}
              />
            </div>
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
                onKeyDown={(e) => e.key === "Enter" && owner && !listMutation.isPending && listMutation.mutate()}
              />
            </div>
            <Button
              onClick={() => listMutation.mutate()}
              disabled={listMutation.isPending || !owner}
              className="mt-1"
            >
              {listMutation.isPending ? (
                <><CircleNotch className="size-3.5 animate-spin" />Loading…</>
              ) : (
                "Load repositories"
              )}
            </Button>
            {!tokenSaved && token && owner && (
              <Button
                variant="outline"
                onClick={() => saveTokenMutation.mutate()}
                disabled={saveTokenMutation.isPending}
              >
                <Key className="size-3.5" />
                {saveTokenMutation.isPending ? "Saving…" : "Save token for this org"}
              </Button>
            )}
            {listMutation.isError && (
              <div className="flex items-start gap-2 text-xs text-destructive">
                <Warning className="size-3.5 mt-0.5 shrink-0" />
                {listMutation.error.message}
              </div>
            )}
          </div>
        </div>

        {/* Repo table */}
        {(listMutation.data || listMutation.isPending) && (
          <div className="bg-card border border-border lg:col-span-2">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
              <span className="section-title">Repositories</span>
              <div className="flex items-center gap-2">
                {listMutation.data && (
                  <>
                    <Input
                      placeholder="Filter by name…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      className="h-7 w-40 text-xs"
                    />
                    <span className="stat-chip">{repos.length} of {listMutation.data.total}</span>
                  </>
                )}
              </div>
            </div>

            {listMutation.isPending ? (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <tbody className="divide-y divide-border">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <tr key={i}>
                        <td className="px-4 py-3"><Skeleton className="h-3 w-32" /></td>
                        <td className="px-4 py-3"><Skeleton className="h-3 w-16" /></td>
                        <td className="px-4 py-3"><Skeleton className="h-3 w-10 ml-auto" /></td>
                        <td className="px-4 py-3"><Skeleton className="h-3 w-10 ml-auto" /></td>
                        <td className="px-4 py-3"><Skeleton className="h-6 w-24" /></td>
                        <td className="px-4 py-3"><Skeleton className="h-3 w-16 ml-auto" /></td>
                        <td className="px-4 py-3"><Skeleton className="h-3 w-10 ml-auto" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : repos.length === 0 ? (
              <div className="px-4 py-8">
                <p className="text-sm text-muted-foreground font-mono">
                  — no repositories match{search ? " your filter" : ""}
                </p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left text-muted-foreground font-medium px-4 py-2">Repository</th>
                      <th className="text-left text-muted-foreground font-medium px-4 py-2">Language</th>
                      <th className="text-right text-muted-foreground font-medium px-4 py-2">Stars</th>
                      <th className="text-right text-muted-foreground font-medium px-4 py-2">Open PRs</th>
                      <th className="text-left text-muted-foreground font-medium px-4 py-2">Activity (8w)</th>
                      <th className="text-right text-muted-foreground font-medium px-4 py-2">Pushed</th>
                      <th className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {repos.map((r) => (
                      <RepoRow key={r.full_name} org={owner.trim()} repo={r} token={loadedToken} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
