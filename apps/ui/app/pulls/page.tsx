"use client"

import { useEffect, useMemo, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { DataTable, type DataTableColumn } from "@/components/ui/data-table"
import { Skeleton } from "@/components/ui/skeleton"
import { SectionError } from "@/components/section-error"
import { EmptyStateNoAccount } from "@/components/empty-state"
import { GitPullRequest } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import { isOrgMemberOnly } from "@/lib/members-href"
import { relativeTime } from "@/lib/format"
import { FlowRankingCard } from "@/components/flow-ranking-card"
import { githubWebUrl } from "@/lib/github-web"
import { PULL_SORT_LABELS, ageDays, isStale, sortPulls, type PullSort } from "@/lib/pull-triage"
import type { MyOrgMembership, PullSummary } from "@/lib/api/types"

// No repo-count cap here; requests are fanned out in batches so large orgs don't fire
// dozens of simultaneous requests.
const REPO_BATCH_SIZE = 10
// Same entries (and key) as the Repositories page rows, so visiting one warms the other.
const REPO_PULLS_STALE_MS = 30_000

interface PullRow extends PullSummary {
  repo: string
}

type GroupBy = "repo" | "author"

// Mirrors the server's default (services/pr_nudge.py), used until a response reports the real one.
const DEFAULT_STALE_DAYS = 3

/** A draft is never stale (the nudge skips it); otherwise stale PRs are the ones the nudge would act on. */
function pullStatus(p: PullRow, staleDays: number): "Draft" | "Stale" | "Open" {
  return p.draft ? "Draft" : isStale(p, staleDays) ? "Stale" : "Open"
}

function DraftChip() {
  return <span className="stat-chip shrink-0">Draft</span>
}

/** "5 days ago", highlighted when the PR is stale: the same PRs "Nudge stale PRs" would act on. */
function Age({ pull, staleDays }: { pull: PullRow; staleDays: number }) {
  const stale = isStale(pull, staleDays)
  return (
    <span
      className={stale ? "text-warning font-medium" : undefined}
      title={stale ? `Open ${ageDays(pull.created_at)} days with no activity for ${staleDays}+ days` : undefined}
    >
      {relativeTime(pull.created_at)}
    </span>
  )
}

const pullColumns = (staleDays: number): DataTableColumn<PullRow>[] => [
  {
    key: "repo",
    header: "Repo",
    sortValue: (p) => p.repo,
    cellClassName: "text-muted-foreground",
    render: (p) => p.repo,
    csvValue: (p) => p.repo,
  },
  {
    key: "pr",
    header: "Pull Request",
    sortValue: (p) => p.title.toLowerCase(),
    render: (p) => (
      <a
        href={p.html_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 text-foreground/90 hover:text-primary transition-colors"
      >
        <GitPullRequest className="size-3.5 shrink-0" />
        #{p.number} {p.title}
      </a>
    ),
    csvValue: (p) => `#${p.number} ${p.title}`,
  },
  {
    key: "author",
    header: "Author",
    sortValue: (p) => (p.user ?? "unknown").toLowerCase(),
    cellClassName: "text-muted-foreground",
    render: (p) => p.user ?? "unknown",
    csvValue: (p) => p.user ?? "unknown",
  },
  {
    key: "opened",
    header: "Opened",
    align: "right",
    sortValue: (p) => p.created_at,
    cellClassName: "text-muted-foreground whitespace-nowrap",
    render: (p) => <Age pull={p} staleDays={staleDays} />,
    csvValue: (p) => p.created_at,
  },
  {
    key: "updated",
    header: "Updated",
    align: "right",
    sortValue: (p) => p.updated_at ?? p.created_at,
    cellClassName: "text-muted-foreground whitespace-nowrap",
    render: (p) => relativeTime(p.updated_at ?? p.created_at),
    csvValue: (p) => p.updated_at ?? p.created_at,
  },
  {
    key: "status",
    header: "Status",
    sortValue: (p) => pullStatus(p, staleDays),
    csvValue: (p) => pullStatus(p, staleDays),
    cellClassName: "whitespace-nowrap",
    render: (p) => {
      const status = pullStatus(p, staleDays)
      return status === "Stale" ? <span className="text-warning font-medium">Stale</span> : <span className="text-muted-foreground">{status}</span>
    },
  },
  {
    key: "url",
    header: "Link",
    render: (p) => (
      <a href={p.html_url} target="_blank" rel="noreferrer" aria-label={`Open ${p.repo} #${p.number} on GitHub`} className="text-primary underline">
        GitHub
      </a>
    ),
    csvValue: (p) => p.html_url,
  },
]

// What a nudge run reports for a PR it acted on: real actions, or the "would-…" ones of a dry-run preview.
const NUDGE_ACTIONS = ["commented", "labeled", "would-comment", "would-label"]

interface NudgedPr {
  repo: string
  number: number
  title: string
  action: string
}
interface SweepOutcome {
  prs: NudgedPr[]
  failed: { repo: string; reason: string }[]
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

function NudgePanel({
  org,
  kind,
  outcome,
  sending,
  onConfirm,
  onDismiss,
}: {
  org: string
  kind: "preview" | "done"
  outcome: SweepOutcome
  sending: boolean
  onConfirm: () => void
  onDismiss: () => void
}) {
  const { prs, failed } = outcome
  const n = prs.length
  const failedText = `${failed.length} ${plural(failed.length, "repository", "repositories")} failed`
  return (
    <div className="px-4 py-3 border-b border-border text-xs space-y-2" role="region" aria-label="Nudge details">
      <p className="text-foreground">
        {kind === "preview"
          ? n === 0
            ? failed.length > 0
              ? "No pull requests need a nudge in the repositories that were checked."
              : "No pull requests need a nudge right now."
            : `These ${n} ${plural(n, "pull request", "pull requests")} would be nudged:`
          : `Nudged ${n} ${plural(n, "pull request", "pull requests")}.${failed.length > 0 ? ` ${failedText}: ${failed.map((f) => f.repo).join(", ")}.` : ""}`}
      </p>
      {n > 0 && (
        <ul className="divide-y divide-border border border-border">
          {prs.map((p) => (
            <li key={`${p.repo}#${p.number}`} className="px-3 py-1.5 flex items-center justify-between gap-3">
              <a
                href={githubWebUrl(`${org}/${p.repo}/pull/${p.number}`)}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 truncate text-primary hover:underline"
              >
                {p.repo} #{p.number} {p.title}
              </a>
              <span className="stat-chip shrink-0">{p.action.replace("would-", "").replace(/ed$/, "")}</span>
            </li>
          ))}
        </ul>
      )}
      {failed.length > 0 && (
        <ul className="text-destructive space-y-0.5">
          {failed.map((f) => (
            <li key={f.repo}>
              {kind === "preview" ? `${f.repo} could not be checked: ` : `${f.repo} failed: `}
              {f.reason}
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-2">
        {kind === "preview" && n > 0 && (
          <Button size="sm" disabled={sending} onClick={onConfirm}>
            {sending ? "Nudging…" : `Nudge ${n} ${plural(n, "pull request", "pull requests")}`}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={sending} onClick={onDismiss}>
          {kind === "preview" && n > 0 ? "Cancel" : "Dismiss"}
        </Button>
      </div>
    </div>
  )
}

export default function PullRequestsPage() {
  const queryClient = useQueryClient()
  const { scope } = useActiveScope()
  // Pulls are listed via /orgs/{org}/repos; a personal scope has no org to list.
  const org = scope?.kind === "org" ? scope.login : ""
  const hasOrg = org.trim().length > 0

  const [groupBy, setGroupBy] = useState<GroupBy>("repo")
  const [sort, setSort] = useState<PullSort>("newest")

  // Nudging posts on GitHub and needs org admin; plain members would just get a 403.
  const { data: memberships = [] } = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })
  const canNudge = !isOrgMemberOnly(memberships, org)

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
  // The flow ranking covers the most recently pushed repositories, so it needs them in that order.
  const recentlyPushedRepos = useMemo(
    () =>
      [...(reposQuery.data?.repos ?? [])]
        .sort((a, b) => (b.pushed_at ?? "").localeCompare(a.pushed_at ?? ""))
        .map((r) => r.name),
    [reposQuery.data],
  )

  const pullsQuery = useQuery({
    queryKey: ["repos.pulls.all", org, repoNames.join(",")],
    queryFn: async () => {
      const rows: PullRow[] = []
      // A repo whose request fails must not look like a repo with no PRs.
      const failedRepos: string[] = []
      let staleDays: number | undefined
      let firstError = ""
      for (let i = 0; i < repoNames.length; i += REPO_BATCH_SIZE) {
        const batch = repoNames.slice(i, i + REPO_BATCH_SIZE)
        const results = await Promise.all(
          batch.map((repo) =>
            queryClient
              .fetchQuery({
                queryKey: ["repo-pulls", org, repo, token],
                queryFn: () => api.repos.pulls(org, org, repo, token),
                staleTime: REPO_PULLS_STALE_MS,
              })
              .then((r) => {
                staleDays ??= r.stale_days
                return r.pulls.map((p) => ({ ...p, repo }))
              })
              .catch((e: unknown) => {
                failedRepos.push(repo)
                if (!firstError && e instanceof Error) firstError = e.message
                return [] as PullRow[]
              }),
          ),
        )
        rows.push(...results.flat())
      }
      if (repoNames.length > 0 && failedRepos.length === repoNames.length) {
        throw new Error(firstError || "Failed to load pull requests.")
      }
      return { rows, failedRepos, staleDays: staleDays ?? DEFAULT_STALE_DAYS }
    },
    enabled: queriesEnabled && !!reposQuery.data,
    retry: false,
  })

  const staleDays = pullsQuery.data?.staleDays ?? DEFAULT_STALE_DAYS
  const columns = useMemo(() => pullColumns(staleDays), [staleDays])
  const loadedRows = pullsQuery.data?.rows
  const pulls = useMemo(() => sortPulls(loadedRows ?? [], sort), [loadedRows, sort])
  const failedRepos = pullsQuery.data?.failedRepos ?? []
  const isLoading = reposQuery.isLoading || (reposQuery.isSuccess && pullsQuery.isLoading)

  // Nudging posts public comments on teammates' PRs, so it is two steps: a dry-run preview lists exactly which
  // PRs would be touched, and only confirming that list sends the real run.
  const [nudgeMsg, setNudgeMsg] = useState<string | null>(null)
  const [panel, setPanel] = useState<{ kind: "preview" | "done"; outcome: SweepOutcome } | null>(null)

  async function sweepAll(dryRun: boolean): Promise<SweepOutcome> {
    const prs: NudgedPr[] = []
    const failed: { repo: string; reason: string }[] = []
    let sawPermissionError = false
    for (let i = 0; i < repoNames.length; i += REPO_BATCH_SIZE) {
      const batch = repoNames.slice(i, i + REPO_BATCH_SIZE)
      const batches = await Promise.all(
        batch.map((repo) =>
          (dryRun ? api.prNudges.sweep(org, org, repo, token, true) : api.prNudges.sweep(org, org, repo, token))
            .then((r) => {
              // The API stops a repository's sweep at a GitHub error after some PRs were handled.
              if (r.results.some((x) => x.action === "error")) {
                failed.push({ repo, reason: "the sweep stopped early after a GitHub error; later pull requests were not processed" })
              }
              return r.results
                .filter((x) => NUDGE_ACTIONS.includes(x.action))
                .map((x) => ({ repo, number: x.number, title: x.title, action: x.action }))
            })
            .catch((e: unknown) => {
              const reason = e instanceof Error ? e.message : "request failed"
              failed.push({ repo, reason })
              if (/pull requests|permission/i.test(reason)) sawPermissionError = true
              return [] as NudgedPr[]
            }),
        ),
      )
      prs.push(...batches.flat())
    }
    // Actions that already happened are shown even when every repo stopped early: dropping them would hide
    // comments that were really posted.
    if (failed.length === repoNames.length && repoNames.length > 0 && prs.length === 0) {
      // Every repo failed — lead with the most likely cause when it's a permission
      // error, otherwise report the failure plainly rather than guessing.
      const detail = failed.map((f) => `${f.repo}: ${f.reason}`).join("; ")
      throw new Error(
        sawPermissionError
          ? `Couldn't nudge any repository — the GitHub App may be missing the 'Pull requests: write' permission. See docs/self-hosting.md. (${detail})`
          : `Couldn't nudge any repository (${failed.length} failed): ${detail}`,
      )
    }
    return { prs, failed }
  }

  const preview = useMutation({
    mutationFn: () => sweepAll(true),
    onSuccess: (outcome) => setPanel({ kind: "preview", outcome }),
    onError: (e) => setNudgeMsg(e instanceof Error ? e.message : "Nudge preview failed."),
  })
  const nudge = useMutation({
    mutationFn: () => sweepAll(false),
    onSuccess: (outcome) => setPanel({ kind: "done", outcome }),
    onError: (e) => {
      setPanel(null)
      setNudgeMsg(e instanceof Error ? e.message : "Nudge failed.")
    },
  })

  const byAuthor = new Map<string, PullRow[]>()
  for (const p of pulls) {
    const author = p.user ?? "unknown"
    const existing = byAuthor.get(author)
    if (existing) existing.push(p)
    else byAuthor.set(author, [p])
  }

  return (
    <>
      <PageHeader title="Pull Requests" description="Open PRs across your organization." />
      <div className="card">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
          <span className="section-title">Open Pull Requests</span>
          <div className="flex items-center gap-3">
            {pulls.length > 0 && <span className="stat-chip">{pulls.length} total</span>}
            {canNudge && <Button
              size="sm"
              variant="outline"
              disabled={pulls.length === 0 || preview.isPending || nudge.isPending}
              onClick={() => {
                setNudgeMsg(null)
                setPanel(null)
                preview.mutate()
              }}
            >
              {preview.isPending ? "Checking…" : nudge.isPending ? "Nudging…" : "Nudge stale PRs"}
            </Button>}
            <select
              aria-label="Sort pull requests"
              value={sort}
              onChange={(e) => setSort(e.target.value as PullSort)}
              className="bg-elevated border border-border rounded-md text-xs text-muted-foreground px-2 py-1 focus:outline-none focus:border-primary"
            >
              {(Object.keys(PULL_SORT_LABELS) as PullSort[]).map((k) => (
                <option key={k} value={k}>{PULL_SORT_LABELS[k]}</option>
              ))}
            </select>
            <div className="flex items-center gap-1.5" role="group" aria-label="Group pull requests by">
              {(["repo", "author"] as const).map((g) => (
                <button
                  key={g}
                  type="button"
                  onClick={() => setGroupBy(g)}
                  aria-pressed={groupBy === g}
                  className={`text-xs font-medium px-2.5 py-1 rounded-md border transition-colors ${
                    groupBy === g
                      ? "border-border bg-elevated text-foreground"
                      : "border-transparent text-muted-foreground hover:bg-elevated"
                  }`}
                >
                  by {g}
                </button>
              ))}
            </div>
          </div>
        </div>
        {nudgeMsg && (
          <p className="px-4 py-2 text-xs text-muted-foreground border-b border-border">{nudgeMsg}</p>
        )}
        {panel && (
          <NudgePanel
            org={org}
            kind={panel.kind}
            outcome={panel.outcome}
            sending={nudge.isPending}
            onConfirm={() => nudge.mutate()}
            onDismiss={() => setPanel(null)}
          />
        )}
        {failedRepos.length > 0 && !pullsQuery.isError && (
          <p role="alert" className="px-4 py-2 text-xs text-destructive border-b border-border">
            {failedRepos.length} repositor{failedRepos.length === 1 ? "y" : "ies"} could not be loaded
            ({failedRepos.slice(0, 5).join(", ")}{failedRepos.length > 5 ? ", …" : ""}), so this list may be incomplete.{" "}
            <button
              type="button"
              className="underline underline-offset-2"
              onClick={() => pullsQuery.refetch()}
              disabled={pullsQuery.isFetching}
            >
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
        ) : pullsQuery.isError ? (
          <SectionError
            message={pullsQuery.error instanceof Error ? pullsQuery.error.message : "Failed to load pull requests."}
            onRetry={() => pullsQuery.refetch()}
            retrying={pullsQuery.isFetching}
          />
        ) : isLoading ? (
          <div className="p-4 flex flex-col gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : pulls.length === 0 ? (
          <p className="px-4 py-8 text-sm text-muted-foreground">
            {failedRepos.length > 0 ? "No open pull requests in the repositories that loaded" : "No open pull requests"}
          </p>
        ) : groupBy === "author" ? (
          <div className="p-4 grid gap-3 sm:grid-cols-2">
            {[...byAuthor.entries()].map(([author, authorPulls]) => (
              <div key={author} className="border border-border/60 rounded-md p-3">
                <p className="text-xs font-medium text-foreground mb-2">
                  {author} <span className="text-muted-foreground font-normal">· {authorPulls.length}</span>
                </p>
                <ul className="flex flex-col gap-1.5">
                  {authorPulls.map((p) => (
                    <li key={`${p.repo}-${p.number}`}>
                      <a
                        href={p.html_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-muted-foreground hover:text-foreground transition-colors truncate block"
                      >
                        <span className="text-foreground/60">{p.repo}</span> #{p.number} {p.title}
                        {p.draft && <> <DraftChip /></>}
                        {" · "}
                        <Age pull={p} staleDays={staleDays} />
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        ) : (
          <DataTable
            columns={columns}
            data={pulls}
            getRowKey={(p) => `${p.repo}-${p.number}`}
            pageSize={50}
            exportCsv={{
              name: `pull-requests-${org}`,
              note: failedRepos.length > 0 ? "Excludes repositories that could not be loaded." : undefined,
            }}
          />
        )}
      </div>
      {hasOrg && repoNames.length > 0 && <FlowRankingCard key={org} org={org} repos={recentlyPushedRepos} token={token} />}
    </>
  )
}
