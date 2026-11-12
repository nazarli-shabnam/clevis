"use client"

import { useState } from "react"
import Link from "next/link"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { formatHours } from "@/components/flow-metrics-card"
import { api } from "@/lib/api/client"
import type { RepoFlowMetricsResponse } from "@/lib/api/types"
import {
  RANKING_REPO_CAP,
  sortRanking,
  toRankingRow,
  type FlowRankingKey,
  type FlowRankingRow,
} from "@/lib/flow-ranking"

// Flow metrics are several GitHub requests per repo, so fewer run at once than elsewhere.
const BATCH_SIZE = 5
const STALE_MS = 10 * 60_000 // the API caches the result for 10 minutes too

const COLUMNS: { key: FlowRankingKey; label: string }[] = [
  { key: "cycleHours", label: "Median cycle time" },
  { key: "reviewHours", label: "Time to first review" },
  { key: "failedRunRate", label: "Failed runs" },
  { key: "mergedPrs", label: "Merged PRs" },
]

const percent = (rate: number | null) => (rate == null ? "—" : `${Math.round(rate * 100)}%`)

interface RankingResult {
  rows: FlowRankingRow[]
  failed: string[]
}

/**
 * Org-wide ranking of repos by PR cycle time, review latency and failed-run rate, so "which repos have the
 * slowest reviews?" doesn't need a visit to each repo. On demand and capped: it loads the flow metrics of the
 * `RANKING_REPO_CAP` most recently pushed repositories, in small batches, through the same cached query as the
 * repo page's Delivery flow card. `repos` is expected newest-pushed first.
 */
export function FlowRankingCard({ org, repos, token }: { org: string; repos: string[]; token: string }) {
  const queryClient = useQueryClient()
  const [sortKey, setSortKey] = useState<FlowRankingKey>("cycleHours")
  const [direction, setDirection] = useState<"desc" | "asc">("desc")
  const covered = repos.slice(0, RANKING_REPO_CAP)

  // Shares the repo page's cache entry (same key), so a ranked repo opens instantly and vice versa. Plain
  // get/set rather than fetchQuery: a failed fetch must just count as a failed repo here.
  async function loadMetrics(repo: string) {
    const key = ["repo-flow-metrics", org, org, repo, token]
    const state = queryClient.getQueryState<RepoFlowMetricsResponse>(key)
    if (state?.data && Date.now() - state.dataUpdatedAt < STALE_MS) return state.data
    const fresh = await api.repos.flowMetrics(org, org, repo, token)
    queryClient.setQueryData(key, fresh)
    return fresh
  }

  const load = useMutation<RankingResult>({
    mutationFn: async () => {
      const rows: FlowRankingRow[] = []
      const failed: string[] = []
      for (let i = 0; i < covered.length; i += BATCH_SIZE) {
        await Promise.all(
          covered.slice(i, i + BATCH_SIZE).map((repo) =>
            loadMetrics(repo)
              .then((m) => void rows.push(toRankingRow(repo, m)))
              .catch(() => void failed.push(repo)),
          ),
        )
      }
      return { rows, failed }
    },
  })

  const sorted = load.data ? sortRanking(load.data.rows, sortKey, direction) : []
  function sortBy(key: FlowRankingKey) {
    if (key === sortKey) setDirection((d) => (d === "desc" ? "asc" : "desc"))
    else {
      setSortKey(key)
      setDirection("desc")
    }
  }

  return (
    <div className="card mt-4">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
        <span className="section-title">Delivery flow by repository</span>
        <Button size="sm" variant="outline" disabled={covered.length === 0 || load.isPending} onClick={() => load.mutate()}>
          {load.isPending ? "Loading…" : load.data ? "Reload" : "Rank repositories"}
        </Button>
      </div>
      {!load.data && !load.isPending && (
        <p className="p-4 text-sm text-muted-foreground">
          Rank repositories by PR cycle time, review latency and failed workflow runs.
          {repos.length > RANKING_REPO_CAP && ` Covers the ${RANKING_REPO_CAP} most recently pushed of ${repos.length} repositories.`}
        </p>
      )}
      {load.data && load.data.rows.length === 0 && load.data.failed.length > 0 && (
        <p className="p-4 text-xs text-destructive">
          Couldn&apos;t load flow metrics for any repository ({load.data.failed.length} failed).
        </p>
      )}
      {load.data && load.data.rows.length > 0 && (
        <>
          {repos.length > RANKING_REPO_CAP && (
            <p className="px-4 pt-3 text-xs text-muted-foreground">
              Ranking the {RANKING_REPO_CAP} most recently pushed of {repos.length} repositories; the rest are not included.
            </p>
          )}
          {load.data.failed.length > 0 && (
            <p role="alert" className="px-4 pt-3 text-xs text-destructive">
              Couldn&apos;t load {load.data.failed.length} repositor{load.data.failed.length === 1 ? "y" : "ies"}:{" "}
              {load.data.failed.join(", ")}.
            </p>
          )}
          <table className="w-full text-xs mt-2">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th className="font-medium px-4 py-2 text-left">Repository</th>
                {COLUMNS.map((c) => (
                  <th
                    key={c.key}
                    className="font-medium px-4 py-2 text-right"
                    aria-sort={c.key === sortKey ? (direction === "desc" ? "descending" : "ascending") : "none"}
                  >
                    <button type="button" onClick={() => sortBy(c.key)} className="hover:text-foreground">
                      {c.label}
                      {c.key === sortKey ? (direction === "desc" ? " ↓" : " ↑") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sorted.map((r) => (
                <tr key={r.repo}>
                  <td className="px-4 py-2">
                    <Link href={`/repos/${encodeURIComponent(org)}~${encodeURIComponent(r.repo)}`} className="text-primary hover:underline">
                      {r.repo}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-right font-mono">{formatHours(r.cycleHours)}</td>
                  <td className="px-4 py-2 text-right font-mono">{formatHours(r.reviewHours)}</td>
                  <td className="px-4 py-2 text-right font-mono">{percent(r.failedRunRate)}</td>
                  <td className="px-4 py-2 text-right font-mono">{r.mergedPrs}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
