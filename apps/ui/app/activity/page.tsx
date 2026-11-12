"use client"

import { Suspense, useEffect, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { ActivityByRepoCard, DEFAULT_ACTIVITY_DAYS } from "@/components/activity-by-repo-card"
import { ActivityList } from "@/components/activity-list"
import { EventFeed } from "@/components/event-feed"
import { HeatmapCalendar } from "@/components/charts/heatmap-calendar"
import { SectionError } from "@/components/section-error"
import { EmptyStateNoAccount } from "@/components/empty-state"
import { ArrowRight } from "@phosphor-icons/react"
import { CHART_COLORS } from "@/lib/charts/theme"
import { relativeTime } from "@/lib/format"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import { writeActivityLastSeen } from "@/lib/activity-last-seen"
import { useAuth } from "@/lib/auth-context"

const WINDOW_OPTIONS = [
  { days: 1, label: "24h" },
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
] as const

const EVENTS_REFRESH_SECONDS = 30
const JOBS_SHOWN = 20
const HEATMAP_COLOR_SCALE = [CHART_COLORS.grid, "#1d4ed8", "#3b82f6", "#60a5fa", "#93c5fd"]

const queryErrorMessage = (error: unknown, fallback: string) => (error instanceof Error ? error.message : fallback)

function ActivityView() {
  const { scope } = useActiveScope()
  const scopeKind = scope?.kind
  const scopeLogin = scope?.login

  // The by-repo window lives in the URL (?days=) so a view can be shared. Read once after mount to avoid a
  // hydration mismatch, then mirrored with replaceState (no history entries). It is also re-read whenever the
  // search params change, so a same-route navigation (Back/Forward, a link to /activity) moves the selector too.
  const [days, setDays] = useState<number>(DEFAULT_ACTIVITY_DAYS)
  const [daysRestored, setDaysRestored] = useState(false)
  const searchParams = useSearchParams()
  const daysParam = searchParams?.get("days") ?? null
  useEffect(() => {
    const fromUrl = Number(daysParam)
    setDays(WINDOW_OPTIONS.some((o) => o.days === fromUrl) ? fromUrl : DEFAULT_ACTIVITY_DAYS)
    setDaysRestored(true)
  }, [daysParam])
  useEffect(() => {
    if (!daysRestored) return
    const params = new URLSearchParams(window.location.search)
    if (days === DEFAULT_ACTIVITY_DAYS) params.delete("days")
    else params.set("days", String(days))
    const qs = params.toString()
    window.history.replaceState(null, "", `${window.location.pathname}${qs ? `?${qs}` : ""}`)
  }, [daysRestored, days])

  // Marks the active account's cockpit events as read so the sidebar's unread badge clears. Stamped again
  // on leaving (or switching account, which re-runs this effect), so events that arrived while the page
  // was open don't come back as unread, and on pagehide for a tab that is closed rather than navigated away.
  useEffect(() => {
    if (!scopeKind || !scopeLogin) return
    const stamp = () => writeActivityLastSeen(scopeKind, scopeLogin)
    stamp()
    window.addEventListener("pagehide", stamp)
    return () => {
      window.removeEventListener("pagehide", stamp)
      stamp()
    }
  }, [scopeKind, scopeLogin])

  // /jobs is workspace-admin only; don't poll a 403 for everyone else.
  const { user } = useAuth()
  const isWorkspaceAdmin = !!user?.is_workspace_admin
  const { data: jobs = [], isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 15_000,
    enabled: isWorkspaceAdmin,
  })

  const org = scope?.login ?? ""
  // Events, failed runs and releases are org endpoints; a personal account would 404.
  const isOrg = scope?.kind === "org"

  // The API resolves the installation token (or a workspace admin's saved PAT) server-side.
  const token = ""
  const hasOrg = org.trim().length > 0
  const queriesEnabled = hasOrg

  const eventsQuery = useQuery({
    queryKey: ["github.events", org],
    queryFn: () => api.github.events(org, token),
    enabled: queriesEnabled && isOrg,
    retry: false,
    refetchInterval: EVENTS_REFRESH_SECONDS * 1000,
  })

  // Heatmap comes from the personal cockpit endpoint (no org membership needed); the same
  // resolved token works for it and the org-scoped calls below.
  const cockpitQuery = useQuery({
    queryKey: ["analytics.cockpit", org],
    queryFn: () => api.analytics.cockpit(org, token),
    enabled: queriesEnabled,
    retry: false,
  })

  const failedRunsQuery = useQuery({
    queryKey: ["github.failed-runs", org],
    queryFn: () => api.github.failedRuns(org, token),
    enabled: queriesEnabled && isOrg,
    retry: false,
  })

  const releaseTimelineQuery = useQuery({
    queryKey: ["github.release-timeline", org],
    queryFn: () => api.github.releaseTimeline(org, token),
    enabled: queriesEnabled && isOrg,
    retry: false,
  })

  return (
    <>
      <PageHeader
        title="Activity"
        description="Recent GitHub activity and background jobs."
        actions={
          <Link
            href="/pulls"
            className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            Open pull requests
            <ArrowRight className="size-3.5" />
          </Link>
        }
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className={`${isWorkspaceAdmin ? "lg:col-span-2" : "lg:col-span-3"} card`}>
          <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
            <span className="section-label">Activity Feed</span>
            {hasOrg && <span className="stat-chip">auto-refreshes every {EVENTS_REFRESH_SECONDS}s</span>}
          </div>
          {!hasOrg ? (
            <EmptyStateNoAccount bare />
          ) : !isOrg ? (
            <p className="p-4 text-sm text-muted-foreground">The activity feed is available for organizations.</p>
          ) : eventsQuery.isError ? (
            <SectionError
              message={queryErrorMessage(eventsQuery.error, "Failed to load events.")}
              onRetry={() => eventsQuery.refetch()}
              retrying={eventsQuery.isFetching}
            />
          ) : (
            <EventFeed events={eventsQuery.data?.events ?? []} isLoading={eventsQuery.isLoading} />
          )}
        </div>

        {isWorkspaceAdmin && (
          <div className="lg:col-span-1 card">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between">
              <span className="section-label">Jobs</span>
              <span className="stat-chip">auto-refreshes every 15s</span>
            </div>
            <ActivityList jobs={jobs} isLoading={jobsLoading} limit={JOBS_SHOWN} />
          </div>
        )}
      </div>

      {hasOrg && (
        <div className="grid gap-4 lg:grid-cols-2 mt-4">
          <div className="card lg:col-span-2">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2">
              <span className="section-label">Commit Heatmap (52w)</span>
              {cockpitQuery.data?.commit_activity_source === "aggregate" && (
                <span
                  className="text-[0.6875rem] text-muted-foreground/70"
                  title="Estimated from stored push events, not GitHub's exact commit count."
                >
                  (estimated)
                </span>
              )}
            </div>
            <div className="p-4">
              {cockpitQuery.isError ? (
                <SectionError
                  message={queryErrorMessage(cockpitQuery.error, "Failed to load commit activity.")}
                  onRetry={() => cockpitQuery.refetch()}
                  retrying={cockpitQuery.isFetching}
                />
              ) : (cockpitQuery.data?.commit_heatmap_52w ?? []).some((n) => n > 0) ? (
                <HeatmapCalendar data={cockpitQuery.data!.commit_heatmap_52w} colorScale={HEATMAP_COLOR_SCALE} />
              ) : (
                <p className="text-sm text-muted-foreground">No commit activity in the last year</p>
              )}
            </div>
          </div>

          {isOrg && (<>
          <div className="lg:col-span-2">
            <div className="flex items-center justify-end gap-1.5 mb-2" role="group" aria-label="Activity window">
              {WINDOW_OPTIONS.map((o) => (
                <button
                  key={o.days}
                  onClick={() => setDays(o.days)}
                  aria-pressed={days === o.days}
                  className={`text-xs font-mono px-2 py-1 border transition-colors ${
                    days === o.days
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border text-muted-foreground hover:bg-elevated"
                  }`}
                >
                  {o.label}
                </button>
              ))}
            </div>
            <ActivityByRepoCard org={org} days={days} />
          </div>
          <div className="card">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">CI Failure Log</span>
            </div>
            {failedRunsQuery.isError ? (
              <SectionError
                message={queryErrorMessage(failedRunsQuery.error, "Failed to load CI failures.")}
                onRetry={() => failedRunsQuery.refetch()}
                retrying={failedRunsQuery.isFetching}
              />
            ) : (failedRunsQuery.data?.runs.length ?? 0) === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">No repeated CI failures</p>
            ) : (
              <div className="divide-y divide-border">
                {failedRunsQuery.data!.runs.map((r) => (
                  <a
                    key={`${r.repo}-${r.run_id}`}
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-elevated transition-colors"
                  >
                    <span className="flex flex-col min-w-0">
                      <span className="text-foreground/90 truncate">{r.repo} · {r.workflow_name}</span>
                      <span className="text-[0.6875rem] text-muted-foreground">{r.branch} · {r.actor}</span>
                    </span>
                    <span className="stat-chip text-red-400 border-red-500/30 shrink-0 ml-2">
                      ×{r.consecutive_failures}
                    </span>
                  </a>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-label">Release Timeline</span>
            </div>
            {releaseTimelineQuery.isError ? (
              <SectionError
                message={queryErrorMessage(releaseTimelineQuery.error, "Failed to load releases.")}
                onRetry={() => releaseTimelineQuery.refetch()}
                retrying={releaseTimelineQuery.isFetching}
              />
            ) : (releaseTimelineQuery.data?.releases.length ?? 0) === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">No releases in the last 90 days</p>
            ) : (
              <div className="divide-y divide-border">
                {releaseTimelineQuery.data!.releases.map((r) => (
                  <a
                    key={r.url}
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-elevated transition-colors"
                  >
                    <span className="flex flex-col min-w-0">
                      <span className="text-foreground/90 truncate">
                        {r.repo} · {r.name}
                        {r.is_prerelease && <span className="stat-chip ml-1.5 align-middle">pre-release</span>}
                      </span>
                      <span className="text-[0.6875rem] text-muted-foreground truncate">{r.body_preview}</span>
                    </span>
                    <span className="text-[0.6875rem] text-muted-foreground whitespace-nowrap shrink-0 ml-2">
                      {relativeTime(r.published_at)}
                    </span>
                  </a>
                ))}
              </div>
            )}
          </div>
          </>)}
        </div>
      )}
    </>
  )
}

// useSearchParams must sit under a Suspense boundary or static prerendering bails out.
export default function ActivityPage() {
  return (
    <Suspense fallback={null}>
      <ActivityView />
    </Suspense>
  )
}
