"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { EmptyStateInline } from "@/components/empty-state"
import { SectionError } from "@/components/section-error"
import { api } from "@/lib/api/client"

const ACTION_TYPES = [
  "",
  "cache.clear",
  "cache.clear.dry_run",
  "installation.connected",
  "installation.connected.personal",
]

export default function AuditPage() {
  const [actionFilter, setActionFilter] = useState("")

  const { data: logs = [], isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ["audit", actionFilter],
    queryFn: () => api.audit.list(actionFilter || undefined),
    refetchInterval: 30_000,
  })

  return (
    <>
      <PageHeader title="Audit Log" description="Immutable record of all significant actions." />

      <div className="card">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-4">
          <span className="section-label">Events</span>
          <div className="flex items-center gap-3">
            <select
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className="bg-elevated border border-border rounded-md text-xs text-muted-foreground font-mono px-2 py-1 focus:outline-none focus:border-primary"
            >
              <option value="">all actions</option>
              {ACTION_TYPES.filter(Boolean).map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            {!isLoading && !isError && <span className="stat-chip">{logs.length} entries</span>}
          </div>
        </div>

        {isLoading ? (
          <div className="px-4 py-8">
            <p className="text-sm text-muted-foreground animate-pulse">Loading…</p>
          </div>
        ) : isError ? (
          <SectionError
            message={error instanceof Error ? error.message : "Failed to load audit events."}
            onRetry={() => refetch()}
            retrying={isFetching}
          />
        ) : logs.length === 0 ? (
          <EmptyStateInline noun="audit events" qualifier={actionFilter || undefined} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left section-label px-4 py-2">Actor</th>
                  <th className="text-left section-label px-4 py-2">Action</th>
                  <th className="text-left section-label px-4 py-2">Target</th>
                  <th className="text-right section-label px-4 py-2">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {logs.map((log) => (
                  <tr key={log.id} className="hover:bg-elevated transition-colors">
                    <td className="px-4 py-2.5 font-mono text-foreground/80">{log.actor}</td>
                    <td className="px-4 py-2.5 text-primary font-mono">{log.action}</td>
                    <td className="px-4 py-2.5 text-muted-foreground max-w-[14rem] truncate">{log.target}</td>
                    <td className="px-4 py-2.5 text-right font-mono text-muted-foreground whitespace-nowrap">
                      {new Date(log.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
