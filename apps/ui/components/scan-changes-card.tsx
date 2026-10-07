"use client"

import { useQuery } from "@tanstack/react-query"
import { SectionError } from "@/components/section-error"
import { api } from "@/lib/api/client"
import { relativeTime } from "@/lib/format"
import type { CheckChange } from "@/lib/api/types"

export const scanChangesKey = (org: string) => ["analytics.changes", org] as const

const LABEL: Record<CheckChange["change"], string> = {
  newly_failing: "Now failing",
  still_failing: "Still failing",
  newly_passing: "Fixed",
}
const TONE: Record<CheckChange["change"], string> = {
  newly_failing: "text-destructive border-destructive/30",
  still_failing: "text-muted-foreground",
  newly_passing: "text-accent border-accent/30",
}
const SHOWN_REPOS = 5

/** What moved between the org's two latest scans, so a scheduled scan's result is readable at a glance. */
export function ScanChangesCard({ org }: { org: string }) {
  const query = useQuery({
    queryKey: scanChangesKey(org),
    queryFn: () => api.analytics.changes(org),
    enabled: org.trim().length > 0,
    retry: false,
  })
  const d = query.data

  return (
    <div className="card lg:col-span-2">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <span className="section-title">Changed since the last scan</span>
        {d?.has_previous && d.previous_score !== null && d.score !== null && (
          <span className="stat-chip">
            {d.previous_score} → {d.score}
            {d.comparable ? "" : " (not comparable)"}
          </span>
        )}
      </div>
      {query.isError ? (
        <SectionError
          message={query.error instanceof Error ? query.error.message : "Failed to load scan changes."}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.isLoading ? (
        <p className="p-4 text-sm text-muted-foreground">Loading…</p>
      ) : !d?.has_previous ? (
        <p className="p-4 text-sm text-muted-foreground">
          {d?.scanned_at ? "Only one scan so far; changes appear after the next one." : "No scans yet."}
        </p>
      ) : (
        <>
          <p className="px-4 pt-3 text-xs text-muted-foreground">
            Latest scan {d.scanned_at ? relativeTime(d.scanned_at) : ""} compared with the one{" "}
            {d.previous_scanned_at ? relativeTime(d.previous_scanned_at) : "before"}.
            {!d.comparable &&
              " A check errored or the set of scored checks differs, so the score change may not reflect a real change."}
          </p>
          {d.changes.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">No check changed status.</p>
          ) : (
            <ul className="divide-y divide-border">
              {d.changes.map((c) => (
                <li key={c.id} className="flex items-start justify-between gap-3 px-4 py-2.5 text-sm">
                  <span className="flex min-w-0 flex-col">
                    <span className="text-foreground/90">{c.title}</span>
                    {c.repos.length > 0 && (
                      <span className="text-[0.6875rem] text-muted-foreground">
                        {c.repos.slice(0, SHOWN_REPOS).join(", ")}
                        {c.repos.length > SHOWN_REPOS ? ` and ${c.repos.length - SHOWN_REPOS} more` : ""}
                      </span>
                    )}
                  </span>
                  <span className={`stat-chip shrink-0 ${TONE[c.change]}`}>{LABEL[c.change]}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
