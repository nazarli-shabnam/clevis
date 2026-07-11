"use client"

import { useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useMutation } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { CheckCard } from "@/components/check-card"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { AlertTriangle, KeyRound } from "lucide-react"
import { api } from "@/lib/api/client"
import { shouldApplyResolvedToken } from "@/lib/token-resolve"
import { DonutChart } from "@/components/charts/donut-chart"
import type { CheckResult } from "@/lib/api/types"

function ScoreGauge({ score, failed, total }: { score: number; failed: number; total: number }) {
  const r = 38
  const circumference = 2 * Math.PI * r
  const progress = (score / 100) * circumference
  // Use CSS custom property so it respects the theme's primary blue
  const color = score >= 80 ? "#4ade80" : score >= 50 ? "#facc15" : "#f87171"

  return (
    <div className="flex items-center gap-5 px-4 py-4 border-b border-border">
      <div className="relative shrink-0">
        <svg width="96" height="96" className="-rotate-90" aria-label={`Security score: ${score} out of 100`}>
          <circle cx="48" cy="48" r={r} fill="none" stroke="#1c1c1c" strokeWidth="7" />
          <circle
            cx="48" cy="48" r={r} fill="none"
            stroke={color} strokeWidth="7"
            strokeDasharray={`${progress} ${circumference}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dasharray 0.6s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="text-xl font-bold tabular-nums" style={{ color }}>{score}</span>
        </div>
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground">Security Score</p>
        <div className="flex items-center gap-2 mt-2">
          <span className="stat-chip">{total - failed} passed</span>
          {failed > 0 && <span className="stat-chip text-red-400 border-red-500/30">{failed} failed</span>}
        </div>
      </div>
    </div>
  )
}

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 }

function sortChecks(checks: CheckResult[]): CheckResult[] {
  return [...checks].sort((a, b) => {
    // Failed before passed
    if (a.status !== b.status) return a.status === "fail" ? -1 : 1
    // Within same status: high → medium → low
    return (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  })
}

export default function SecurityPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [owner, setOwner] = useState("")
  const [token, setToken] = useState("")
  const [tokenSaved, setTokenSaved] = useState(false)

  const statusFilter   = (searchParams.get("status")   ?? "all") as "all" | "pass" | "fail"
  const severityFilter = (searchParams.get("severity") ?? "all") as "all" | "high" | "medium" | "low"

  function setFilter(key: "status" | "severity", value: string) {
    const params = new URLSearchParams(searchParams.toString())
    if (value === "all") params.delete(key)
    else params.set(key, value)
    router.replace(`?${params.toString()}`, { scroll: false })
  }

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

  const scan = useMutation({
    mutationFn: () => api.analytics.overview(owner, token),
  })

  const filteredChecks = scan.data
    ? sortChecks(
        scan.data.checks.filter((c) => {
          if (statusFilter   !== "all" && c.status   !== statusFilter)   return false
          if (severityFilter !== "all" && c.severity !== severityFilter) return false
          return true
        }),
      )
    : []

  const allChecks = scan.data?.checks ?? []
  const statusBreakdown = [
    { name: "Passed", value: allChecks.filter((c) => c.status === "pass").length, color: "#34d399" },
    { name: "Failed · high", value: allChecks.filter((c) => c.status === "fail" && c.severity === "high").length, color: "#f87171" },
    { name: "Failed · med/low", value: allChecks.filter((c) => c.status === "fail" && c.severity !== "high").length, color: "#fbbf24" },
  ].filter((d) => d.value > 0)

  return (
    <>
      <PageHeader
        title="Health & Security"
        description="Run security checks against a GitHub organization."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Config */}
        <div className="bg-card border border-border">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-title">Scan configuration</span>
          </div>
          <div className="p-4 flex flex-col gap-3">
            <div>
              <label className="text-xs font-medium text-foreground block mb-1.5">Organization</label>
              <Input
                placeholder="e.g. octocat"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && owner && token && !scan.isPending && scan.mutate()}
              />
            </div>
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
                onKeyDown={(e) => e.key === "Enter" && owner && token && !scan.isPending && scan.mutate()}
              />
            </div>
            <Button
              onClick={() => scan.mutate()}
              disabled={scan.isPending || !owner || !token}
              className="mt-1"
            >
              {scan.isPending ? "Scanning…" : "Run scan"}
            </Button>
            {!tokenSaved && token && owner && (
              <Button
                variant="outline"
                onClick={() => saveTokenMutation.mutate()}
                disabled={saveTokenMutation.isPending}
              >
                <KeyRound className="size-3.5" />
                {saveTokenMutation.isPending ? "Saving…" : "Save token for this org"}
              </Button>
            )}
            {scan.isError && (
              <div className="flex items-start gap-2 text-xs text-destructive">
                <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
                {scan.error.message}
              </div>
            )}
          </div>
        </div>

        {/* Results */}
        {(scan.data || scan.isPending) && (
          <div className="bg-card border border-border lg:col-span-2">
            {scan.data && (
              <>
                <div className="px-4 py-3 border-b border-border">
                  <span className="section-title">Results — {scan.data.owner}</span>
                </div>
                <ScoreGauge
                  score={scan.data.score}
                  failed={scan.data.failed_checks}
                  total={scan.data.total_checks}
                />
              </>
            )}

            {/* Filter bar */}
            {scan.data && (
              <div className="px-4 py-2.5 border-b border-border flex items-center gap-3">
                <select
                  value={statusFilter}
                  onChange={(e) => setFilter("status", e.target.value)}
                  className="text-xs bg-card border border-border text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="all">All statuses</option>
                  <option value="fail">Failed only</option>
                  <option value="pass">Passed only</option>
                </select>
                <select
                  value={severityFilter}
                  onChange={(e) => setFilter("severity", e.target.value)}
                  className="text-xs bg-card border border-border text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                >
                  <option value="all">All severities</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
                <span className="text-xs text-muted-foreground ml-auto">
                  {filteredChecks.length} of {scan.data.checks.length}
                </span>
              </div>
            )}

            {/* Pass/fail breakdown */}
            {scan.data && statusBreakdown.length > 0 && (
              <div className="px-4 py-4 border-b border-border">
                <DonutChart data={statusBreakdown} label="checks" height={180} />
              </div>
            )}

            <div className="p-4 grid gap-3 sm:grid-cols-2">
              {scan.isPending ? (
                /* Skeleton while scanning */
                Array.from({ length: 3 }).map((_, i) => (
                  <div
                    key={i}
                    className="bg-card border border-border/40 p-3.5 flex items-start gap-3 animate-pulse"
                  >
                    <Skeleton className="size-4 shrink-0 mt-0.5 rounded-full" />
                    <div className="flex-1 space-y-2">
                      <Skeleton className="h-3 w-3/4" />
                      <Skeleton className="h-2.5 w-full" />
                      <Skeleton className="h-2.5 w-1/2" />
                    </div>
                  </div>
                ))
              ) : filteredChecks.length === 0 ? (
                <p className="text-sm text-muted-foreground font-mono sm:col-span-2">
                  — no checks match the current filter
                </p>
              ) : (
                filteredChecks.map((c: CheckResult) => (
                  <CheckCard key={c.id} check={c} />
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </>
  )
}
