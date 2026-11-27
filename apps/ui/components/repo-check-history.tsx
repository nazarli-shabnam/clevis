"use client"

import { useMemo } from "react"
import { useQuery } from "@tanstack/react-query"
import { SectionError } from "@/components/section-error"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/time"
import { api, normalizeCheckValue } from "@/lib/api/client"
import { repoCheckHistory, type RepoCheckHistory, type RepoCheckState, type StoredCheck } from "@/lib/scan-compare"

const WINDOW_DAYS = 180
const SHOWN_SCANS = 30

const STATE_STYLE: Record<RepoCheckState, { box: string; word: string }> = {
  fail: { box: "bg-destructive", word: "failing" },
  pass: { box: "bg-emerald-500", word: "passing" },
  unknown: { box: "bg-muted-foreground/30", word: "not assessed" },
}

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })

function Timeline({ item }: { item: RepoCheckHistory }) {
  const shown = item.points.slice(-SHOWN_SCANS)
  const label = `${item.title}: ${shown.map((p) => STATE_STYLE[p.state].word).join(", ")} across the last ${shown.length} scans, oldest first`
  const { word } = STATE_STYLE[item.current]
  return (
    <li className="px-4 py-2.5 text-xs">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate text-foreground/90">{item.title}</span>
        <span className="shrink-0 text-muted-foreground">
          {word} since {item.sinceAtLeast ? "at least " : ""}
          <Time iso={item.since} />
        </span>
      </div>
      <div role="img" aria-label={label} className="mt-1.5 flex gap-0.5">
        {shown.map((p) => (
          <span
            key={p.scanId}
            title={`${STATE_STYLE[p.state].word} · scan of ${day(p.at)}`}
            className={`h-3 w-2 rounded-[2px] ${STATE_STYLE[p.state].box}`}
          />
        ))}
      </div>
    </li>
  )
}

/** When this repository started failing each per-repository check, from the org's stored scans: one strip per
 * check, a square per scan (oldest to newest). Only checks the repo ever failed within the window are listed. */
export function RepoCheckHistoryCard({ owner, repo, enabled }: { owner: string; repo: string; enabled: boolean }) {
  const since = useMemo(() => new Date(Date.now() - WINDOW_DAYS * 86_400_000).toISOString().slice(0, 10), [])
  const query = useQuery({
    queryKey: ["analytics.scans", owner, since],
    queryFn: () => api.analytics.exportHistory(owner, since),
    enabled: enabled && owner.trim().length > 0,
    retry: false,
  })
  const items = useMemo(
    () =>
      repoCheckHistory(
        (query.data?.entries ?? []).map((s) => ({
          id: s.id,
          created_at: s.created_at,
          checks: ((s.checks ?? []) as StoredCheck[]).map((c) =>
            c.value == null ? c : { ...c, value: normalizeCheckValue(c.id ?? "", c.value) },
          ),
        })),
        repo,
      ),
    [query.data, repo],
  )

  return (
    <div className="mt-4 border-t border-border pt-4">
      <p className="section-label mb-2">Check history · last {WINDOW_DAYS} days</p>
      {query.isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : query.isError ? (
        <SectionError message={query.error.message} onRetry={() => query.refetch()} retrying={query.isFetching} />
      ) : items.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {query.data?.truncated ? "The scan history is too long to show here." : "No stored scan shows this repository failing a check."}
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {items.map((item) => (
            <Timeline key={item.id} item={item} />
          ))}
        </ul>
      )}
    </div>
  )
}
