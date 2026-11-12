"use client"

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import { checkValueSummary, diffScans, isFailing, scanCounts, type StoredCheck } from "@/lib/scan-compare"
import type { ScanExportEntry } from "@/lib/api/types"

// Rows replayed from older runners may lack `checks`; read them as empty rather than failing the whole list.
const checksOf = (scan: ScanExportEntry) => (scan.checks ?? []) as StoredCheck[]

const SHOWN_SCANS = 30
const WINDOW_DAYS = 180

const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })

function CheckRows({ checks, empty }: { checks: StoredCheck[]; empty: string }) {
  if (checks.length === 0) return <p className="px-4 py-3 text-sm text-muted-foreground">{empty}</p>
  return (
    <ul className="divide-y divide-border">
      {checks.map((c) => (
        <li key={c.id ?? c.title} className="flex items-start justify-between gap-3 px-4 py-2.5 text-sm">
          <span className="flex min-w-0 flex-col">
            <span className="text-foreground/90">{c.title ?? c.id}</span>
            <span className="text-[0.6875rem] text-muted-foreground">
              {[c.severity, checkValueSummary(c.value)].filter(Boolean).join(" · ")}
            </span>
          </span>
          <span className="flex shrink-0 gap-1.5">
            {c.waived && <span className="stat-chip">waived</span>}
            <span className={`stat-chip ${c.status === "error" ? "text-amber-500" : "text-destructive border-destructive/30"}`}>
              {c.status}
            </span>
          </span>
        </li>
      ))}
    </ul>
  )
}

function ScanDetail({ scan }: { scan: ScanExportEntry }) {
  const checks = checksOf(scan)
  const counts = scanCounts(checks)
  return (
    <div role="region" aria-label="Scan details">
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Scan from {when(scan.created_at)} · score {scan.score ?? "n/a"} · {counts.failed} failed, {counts.errored} errored,{" "}
        {counts.waived} waived
      </p>
      <CheckRows checks={checks.filter(isFailing)} empty="No failing or errored checks in this scan." />
    </div>
  )
}

function Comparison({ from, to }: { from: ScanExportEntry; to: ScanExportEntry }) {
  const diff = useMemo(() => diffScans(checksOf(from), checksOf(to)), [from, to])
  return (
    <div role="region" aria-label="Scan comparison">
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        {when(from.created_at)} (score {from.score ?? "n/a"}) → {when(to.created_at)} (score {to.score ?? "n/a"})
      </p>
      <h4 className="px-4 pt-3 text-xs font-medium text-muted-foreground">Newly failing ({diff.newlyFailing.length})</h4>
      <CheckRows checks={diff.newlyFailing} empty="Nothing newly failing." />
      <h4 className="px-4 pt-3 text-xs font-medium text-muted-foreground">Fixed ({diff.fixed.length})</h4>
      <CheckRows checks={diff.fixed} empty="Nothing fixed." />
      <h4 className="px-4 pt-3 text-xs font-medium text-muted-foreground">Changed value ({diff.changed.length})</h4>
      {diff.changed.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">No failing check changed value.</p>
      ) : (
        <ul className="divide-y divide-border">
          {diff.changed.map(({ check, before, after }) => (
            <li key={check.id} className="flex items-start justify-between gap-3 px-4 py-2.5 text-sm">
              <span className="text-foreground/90">{check.title ?? check.id}</span>
              <span className="stat-chip shrink-0">
                {before || "—"} → {after || "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Browse the org's stored scans, open any one (also from the score-trend chart) and compare two. The
 * selection lives in `scan` / `compare`, which the page keeps in the URL so a drop can be shared. */
export function ScanExplorer({
  owner,
  scanId,
  compareId,
  onChange,
}: {
  owner: string
  scanId: number | null
  compareId: number | null
  onChange: (scan: number | null, compare: number | null) => void
}) {
  const since = useMemo(() => new Date(Date.now() - WINDOW_DAYS * 86_400_000).toISOString().slice(0, 10), [])
  const query = useQuery({
    queryKey: ["analytics.scans", owner, since],
    queryFn: () => api.analytics.exportHistory(owner, since),
    enabled: owner.trim().length > 0,
    retry: false,
  })
  const scans = useMemo(() => (query.data?.entries ?? []).slice(0, SHOWN_SCANS), [query.data])
  const byId = useMemo(() => new Map((query.data?.entries ?? []).map((s) => [s.id, s])), [query.data])
  const selected = scanId !== null ? byId.get(scanId) : undefined
  const other = compareId !== null ? byId.get(compareId) : undefined
  // Whichever is older is the "from" side, whatever order the two were picked in.
  const ordered =
    selected && other
      ? Date.parse(selected.created_at) <= Date.parse(other.created_at)
        ? ([selected, other] as const)
        : ([other, selected] as const)
      : null

  return (
    <div className="card lg:col-span-2" id="scan-history">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
        <span className="section-title">Scan history</span>
        {scans.length > 1 && (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Compare with
            <select
              aria-label="Compare with"
              value={compareId ?? ""}
              disabled={scanId === null}
              onChange={(e) => onChange(scanId, e.target.value ? Number(e.target.value) : null)}
              className="bg-background border border-border px-2 py-1 text-foreground disabled:opacity-50"
            >
              <option value="">{scanId === null ? "Pick a scan first" : "—"}</option>
              {scans
                .filter((s) => s.id !== scanId)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {when(s.created_at)} · {s.score ?? "n/a"}
                  </option>
                ))}
            </select>
          </label>
        )}
      </div>
      {query.isError ? (
        <SectionError
          message={query.error instanceof Error ? query.error.message : "Failed to load scans."}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.isLoading ? (
        <p className="p-4 text-sm text-muted-foreground">Loading…</p>
      ) : scans.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">No scans in the last {WINDOW_DAYS} days.</p>
      ) : (
        <>
          <ul className="divide-y divide-border max-h-64 overflow-y-auto">
            {scans.map((s) => {
              const counts = scanCounts(checksOf(s))
              return (
                <li key={s.id} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                  <span className="text-foreground/90">{when(s.created_at)}</span>
                  <span className="flex items-center gap-3">
                    <span className="stat-chip">score {s.score ?? "n/a"}</span>
                    <span className="text-[0.6875rem] text-muted-foreground">
                      {counts.failed} failed{counts.errored > 0 ? `, ${counts.errored} errored` : ""}
                    </span>
                    <Button
                      size="sm"
                      variant={s.id === scanId ? "default" : "outline"}
                      aria-pressed={s.id === scanId}
                      aria-label={`View scan from ${when(s.created_at)}`}
                      onClick={() => onChange(s.id, null)}
                    >
                      View
                    </Button>
                  </span>
                </li>
              )
            })}
          </ul>
          {query.data?.truncated && (
            <p className="px-4 py-2 text-xs text-amber-500">Showing a capped slice of the history.</p>
          )}
          {scanId !== null && !selected && (
            <p className="px-4 py-3 text-sm text-muted-foreground border-t border-border">
              That scan is not in the last {WINDOW_DAYS} days of history.
            </p>
          )}
          {ordered ? (
            <div className="border-t border-border">
              <Comparison from={ordered[0]} to={ordered[1]} />
            </div>
          ) : selected ? (
            <div className="border-t border-border">
              <ScanDetail scan={selected} />
            </div>
          ) : null}
        </>
      )}
    </div>
  )
}
