"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { SectionError } from "@/components/section-error"
import { Skeleton } from "@/components/ui/skeleton"
import { AreaTimeChart } from "@/components/charts/area-time-chart"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import { githubWebUrl } from "@/lib/github-web"
import {
  BURNDOWN_WINDOWS,
  DEFAULT_BURNDOWN_VIEW,
  burndownRepoHref,
  burndownViewFromParams,
  burndownViewToParams,
  visibleBurndownRepos,
  type BurndownView,
} from "@/lib/burndown-view"
import type { DependabotBurndown } from "@/lib/api/types"

const days = (value: number | null) => (value == null ? "—" : `${value}d`)

export default function DependabotBurndownPage() {
  const { scope } = useActiveScope()
  const org = scope?.kind === "org" ? scope.login : ""

  // Window, severity, sort and repo live in the URL so a view can be shared and notifications can deep-link.
  // Read once after mount (no hydration mismatch), then mirrored with replaceState (no history entries).
  const [view, setView] = useState<BurndownView>(DEFAULT_BURNDOWN_VIEW)
  const [restored, setRestored] = useState(false)
  useEffect(() => {
    setView(burndownViewFromParams(new URLSearchParams(window.location.search)))
    setRestored(true)
  }, [])
  useEffect(() => {
    if (!restored) return
    const qs = burndownViewToParams(view).toString()
    window.history.replaceState(null, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`)
  }, [restored, view])
  const update = (patch: Partial<BurndownView>) => setView((v) => ({ ...v, ...patch }))

  const query = useQuery<DependabotBurndown>({
    queryKey: ["dependabot-burndown", org, view.days],
    queryFn: () => api.security.dependabotBurndown(org, view.days),
    enabled: !!org && restored,
    retry: false,
  })
  const data = query.data
  const repos = visibleBurndownRepos(data?.repos ?? [], view)

  const chart = (data?.trend ?? []).map((p) => ({ week: p.date.slice(5), value: p.critical + p.high }))
  const totalBreaches = (data?.severities ?? []).reduce((sum, s) => sum + s.breaches, 0)

  return (
    <>
      <PageHeader
        title="Dependabot burn-down"
        description="How long critical and high alerts stay open, against your SLA."
        actions={
          <Link href="/security" className="text-xs text-primary hover:underline">
            Back to Health &amp; Security
          </Link>
        }
      />

      {org && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 mb-4 text-xs" role="group" aria-label="Burn-down controls">
          <label className="flex items-center gap-2 text-muted-foreground">
            Window
            <select
              value={view.days}
              onChange={(e) => update({ days: Number(e.target.value) })}
              className="bg-background border border-border px-2 py-1 text-foreground"
            >
              {BURNDOWN_WINDOWS.map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-muted-foreground">
            Severity
            <select
              value={view.severity}
              onChange={(e) => update({ severity: e.target.value as BurndownView["severity"] })}
              className="bg-background border border-border px-2 py-1 text-foreground"
            >
              <option value="all">Critical + high</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-muted-foreground">
            Sort
            <select
              value={view.sort}
              onChange={(e) => update({ sort: e.target.value as BurndownView["sort"] })}
              className="bg-background border border-border px-2 py-1 text-foreground"
            >
              <option value="age">Oldest alert</option>
              <option value="breaches">Most SLA breaches</option>
            </select>
          </label>
          {view.repo && (
            <button onClick={() => update({ repo: "" })} className="stat-chip hover:bg-elevated" title="Show all repositories">
              {view.repo} ✕
            </button>
          )}
        </div>
      )}

      {!org && <p className="text-sm text-muted-foreground">Select an organization in the sidebar first.</p>}
      {query.isLoading && <Skeleton className="h-40 w-full" />}
      {query.isError && <SectionError message={query.error.message} onRetry={() => query.refetch()} retrying={query.isFetching} />}

      {data && (
        <div className="flex flex-col gap-5">
          <div className="card">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <span className="section-label">Open alerts by severity</span>
              <span className={`stat-chip ${totalBreaches > 0 ? "text-destructive" : ""}`}>
                {totalBreaches} SLA breach{totalBreaches === 1 ? "" : "es"}
              </span>
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="font-medium px-4 py-2 text-left">Severity</th>
                  <th className="font-medium px-4 py-2 text-right">Open</th>
                  <th className="font-medium px-4 py-2 text-right">Median age</th>
                  <th className="font-medium px-4 py-2 text-right">Oldest</th>
                  <th className="font-medium px-4 py-2 text-right">SLA</th>
                  <th className="font-medium px-4 py-2 text-right">Breaches</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.severities.map((s) => (
                  <tr key={s.severity}>
                    <td className="px-4 py-2.5 capitalize">{s.severity}</td>
                    <td className="px-4 py-2.5 text-right font-mono">{s.open}</td>
                    <td className="px-4 py-2.5 text-right font-mono">{days(s.median_age_days)}</td>
                    <td className="px-4 py-2.5 text-right font-mono">{days(s.oldest_age_days)}</td>
                    <td className="px-4 py-2.5 text-right font-mono">{days(s.sla_days)}</td>
                    <td className={`px-4 py-2.5 text-right font-mono ${s.breaches > 0 ? "text-destructive" : ""}`}>
                      {s.sla_days == null ? "—" : s.breaches}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Open critical + high, last {data.window_days} days</span>
            </div>
            <div className="p-4">
              <AreaTimeChart data={chart} label="Open critical + high" height={200} />
            </div>
          </div>

          <div className="card">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Repositories</span>
            </div>
            {data.repos.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted-foreground">No open Dependabot alerts.</p>
            ) : repos.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted-foreground">No repositories match these filters.</p>
            ) : (
              <ul className="divide-y divide-border">
                {repos.map((r) => (
                  <li key={r.repo} className="px-4 py-2.5 flex items-center justify-between gap-3 text-xs">
                    <span className="flex items-center gap-3 min-w-0">
                      {burndownRepoHref(r.repo) ? (
                        <Link href={burndownRepoHref(r.repo)!} className="font-mono text-primary hover:underline truncate">
                          {r.repo}
                        </Link>
                      ) : (
                        <span className="font-mono truncate">{r.repo}</span>
                      )}
                      <a
                        href={githubWebUrl(`${r.repo}/security/dependabot/${r.oldest_alert_number}`)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-muted-foreground hover:text-foreground whitespace-nowrap"
                        aria-label={`Open the oldest alert of ${r.repo} on GitHub`}
                      >
                        oldest alert ↗
                      </a>
                    </span>
                    <span className="text-muted-foreground whitespace-nowrap">
                      {r.open.critical} critical · {r.open.high} high · oldest {days(r.oldest_age_days)}
                      {r.breaches > 0 && <span className="text-destructive"> · {r.breaches} breaching SLA</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p className="text-xs text-muted-foreground">
            Based on alerts received by webhook since the GitHub App was connected (older alerts aren&rsquo;t backfilled).
            Closed alerts use their last update as the close date. SLA days are set in Settings &rarr; Instance Configuration.
          </p>
        </div>
      )}
    </>
  )
}
