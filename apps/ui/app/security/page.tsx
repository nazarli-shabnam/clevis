"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { CheckCard } from "@/components/check-card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { AlertTriangle } from "lucide-react"
import { api } from "@/lib/api/client"
import type { CheckResult } from "@/lib/api/types"

function ScoreGauge({ score, failed, total }: { score: number; failed: number; total: number }) {
  const r = 38
  const circumference = 2 * Math.PI * r
  const progress = (score / 100) * circumference
  const color = score >= 80 ? "#22c55e" : score >= 50 ? "#f59e0b" : "#f87171"

  return (
    <div className="flex items-center gap-5 px-4 py-4 border-b border-border">
      <div className="relative shrink-0">
        <svg width="96" height="96" className="-rotate-90" aria-hidden>
          <circle cx="48" cy="48" r={r} fill="none" stroke="#3f3f46" strokeWidth="7" />
          <circle
            cx="48" cy="48" r={r} fill="none"
            stroke={color} strokeWidth="7"
            strokeDasharray={`${progress} ${circumference}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dasharray 0.6s ease" }}
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center rotate-0">
          <span className="text-xl font-bold tabular-nums" style={{ color }}>{score}</span>
        </div>
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground">Security Score</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {failed} failed · {total} checks total
        </p>
        <div className="flex items-center gap-2 mt-2">
          <span className="stat-chip">{total - failed} passed</span>
          {failed > 0 && <span className="stat-chip text-red-400 border-red-500/30">{failed} failed</span>}
        </div>
      </div>
    </div>
  )
}

export default function SecurityPage() {
  const [owner, setOwner] = useState("")
  const [token, setToken] = useState("")

  const scan = useMutation({
    mutationFn: () => api.analytics.overview(owner, token),
  })

  return (
    <>
      <PageHeader
        title="Health & Security"
        description="Run security checks against a GitHub organization."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="bg-card border border-border rounded-lg">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-title">Scan configuration</span>
          </div>
          <div className="p-4 flex flex-col gap-3">
            <div>
              <label className="text-xs font-medium text-foreground block mb-1.5">
                Organization
              </label>
              <Input
                placeholder="e.g. octocat"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs font-medium text-foreground block mb-1.5">
                GitHub Token
              </label>
              <Input
                placeholder="ghp_..."
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="font-mono"
              />
            </div>
            <Button
              onClick={() => scan.mutate()}
              disabled={scan.isPending || !owner || !token}
              className="mt-1"
            >
              {scan.isPending ? "Scanning…" : "Run scan"}
            </Button>
            {scan.isError && (
              <div className="flex items-start gap-2 text-xs text-destructive">
                <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
                {scan.error.message}
              </div>
            )}
          </div>
        </div>

        {scan.data && (
          <div className="bg-card border border-border rounded-lg lg:col-span-2">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-title">Results — {scan.data.owner}</span>
            </div>

            <ScoreGauge
              score={scan.data.score}
              failed={scan.data.failed_checks}
              total={scan.data.total_checks}
            />

            <div className="p-4 grid gap-3 sm:grid-cols-2">
              {scan.data.checks.map((c: CheckResult) => (
                <CheckCard key={c.id} check={c} />
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  )
}
