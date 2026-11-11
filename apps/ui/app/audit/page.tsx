"use client"

import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "next/navigation"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { EmptyStateInline } from "@/components/empty-state"
import { AuditPayload } from "@/components/audit-payload"
import { SectionError } from "@/components/section-error"
import { AuditFilterForm } from "@/components/audit-filter-form"
import { Button } from "@/components/ui/button"
import { DataTable, type DataTableColumn } from "@/components/ui/data-table"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"
import { EMPTY_FORM, toAuditFilters, type FilterForm } from "@/lib/audit-filters"
import { payloadEntries, payloadSummary } from "@/lib/audit-payload"
import type { AdminAuditFilters, AuditLogOut, JobOut } from "@/lib/api/types"

const JOB_STATUS_COLOR: Record<JobOut["status"], string> = {
  queued:     "text-muted-foreground",
  processing: "text-yellow-400",
  running:    "text-yellow-400",
  done:       "text-accent",
  failed:     "text-destructive",
}

// Pages of the newest-first log; "Load more" fetches the next page below the last id seen, so there
// is no ceiling on how far back it can go.
const PAGE_SIZE = 100

// audit_logs.payload embeds job_id for cache-clear rows, so live job status can be shown inline.
function parseJobId(payload: string): number | null {
  try {
    const parsed: unknown = JSON.parse(payload)
    const jobId = (parsed as { job_id?: unknown })?.job_id
    return typeof jobId === "number" ? jobId : null
  } catch {
    return null
  }
}

export default function AuditPage() {
  // /audit, /audit/actions and /jobs are all workspace-admin only; for anyone else every request
  // would just 403, so none is made and the page says why.
  const { user } = useAuth()
  const isAdmin = !!user?.is_workspace_admin
  const [actionFilter, setActionFilter] = useState("")
  const [form, setForm] = useState<FilterForm>(EMPTY_FORM)
  // Filters apply on submit, not per keystroke: each change is a server query.
  const [applied, setApplied] = useState<FilterForm>(EMPTY_FORM)
  const searchParams = useSearchParams()
  const highlightJobId = Number(searchParams.get("job_id")) || null

  const query = useInfiniteQuery({
    queryKey: ["audit", actionFilter, applied],
    queryFn: ({ pageParam }) => {
      const filters: AdminAuditFilters = {
        ...toAuditFilters(applied),
        action: actionFilter || undefined,
        before_id: pageParam,
        limit: PAGE_SIZE,
      }
      return api.audit.list(filters)
    },
    initialPageParam: undefined as number | undefined,
    // A full page means there may be older rows; the next page starts below the last id seen.
    getNextPageParam: (last) => (last.length === PAGE_SIZE ? last[last.length - 1].id : undefined),
    refetchInterval: 30_000,
    enabled: isAdmin,
  })
  const { isLoading, isFetching, refetch } = query
  const logs: AuditLogOut[] = query.data?.pages.flat() ?? []
  const filtered = JSON.stringify(applied) !== JSON.stringify(EMPTY_FORM)
  // A failed refresh with nothing cached to show (including a cached empty list) is an error, not "no entries".
  const isError = query.isError && logs.length === 0
  const error = query.error

  // The filter options come from the log itself. If this fails the dropdown still works for "all"
  // and for whatever is currently selected.
  const { data: knownActions = [] } = useQuery({
    queryKey: ["audit.actions"],
    queryFn: api.audit.actions,
    enabled: isAdmin,
  })
  const actionOptions = [...new Set([...knownActions, ...(actionFilter ? [actionFilter] : [])])]

  // One shared jobs list matched by id, not one query per row.
  const { data: jobs = [], isError: isJobsError } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: 15_000,
    enabled: isAdmin,
  })
  const jobsById = new Map(jobs.map((j) => [j.id, j]))

  const highlightRef = useRef<HTMLTableRowElement>(null)
  useEffect(() => {
    if (highlightRef.current) {
      highlightRef.current.scrollIntoView({ block: "center", behavior: "smooth" })
    }
  }, [highlightJobId, logs.length])

  const columns: DataTableColumn<AuditLogOut>[] = [
    {
      key: "actor",
      header: "Actor",
      sortValue: (log) => log.actor,
      csvValue: (log) => log.actor,
      cellClassName: "font-mono text-foreground/80",
      render: (log) => log.actor,
    },
    {
      key: "action",
      header: "Action",
      sortValue: (log) => log.action,
      csvValue: (log) => log.action,
      cellClassName: "text-primary font-mono",
      render: (log) => log.action,
    },
    {
      key: "target",
      header: "Target",
      sortValue: (log) => log.target,
      csvValue: (log) => log.target,
      cellClassName: "text-muted-foreground max-w-[14rem] truncate",
      render: (log) => log.target,
    },
    {
      key: "details",
      header: "Details",
      // The whole stored payload, so the export carries what the table can only abbreviate.
      csvValue: (log) => log.payload,
      cellClassName: "text-muted-foreground font-mono max-w-[18rem] truncate",
      render: (log) => payloadSummary(log.payload),
    },
    {
      key: "job_status",
      header: "Job status",
      csvValue: (log) => {
        const jobId = parseJobId(log.payload)
        if (jobId === null) return ""
        // The job list loads separately and is capped, so "not found" is unknown, not "no job".
        return jobsById.get(jobId)?.status ?? "unknown"
      },
      cellClassName: "font-mono",
      render: (log) => {
        const jobId = parseJobId(log.payload)
        const job = jobId !== null ? jobsById.get(jobId) : undefined
        return job ? (
          <span className={`font-medium ${JOB_STATUS_COLOR[job.status]}`}>{job.status}</span>
        ) : jobId !== null && isJobsError ? (
          <span className="text-destructive" title="Failed to load job status">failed to load</span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )
      },
    },
    {
      key: "created_at",
      header: "Time",
      align: "right",
      sortValue: (log) => new Date(log.created_at).getTime(),
      // ISO 8601 (not the locale string shown in the table) so it sorts and parses in a spreadsheet.
      csvValue: (log) => log.created_at,
      cellClassName: "font-mono text-muted-foreground whitespace-nowrap",
      render: (log) => new Date(log.created_at).toLocaleString(),
    },
  ]

  if (!isAdmin) {
    return (
      <>
        <PageHeader title="Audit Log" description="Append-only record of all significant actions." />
        <div className="card px-4 py-6 text-sm text-muted-foreground">
          The audit log is only available to workspace admins.
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader title="Audit Log" description="Append-only record of all significant actions." />

      <div className="card">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-4">
          <span className="section-label">Events</span>
          <div className="flex items-center gap-3">
            <select
              aria-label="Filter by action"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              className="bg-elevated border border-border rounded-md text-xs text-muted-foreground font-mono px-2 py-1 focus:outline-none focus:border-primary"
            >
              <option value="">all actions</option>
              {actionOptions.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
            {!isLoading && !isError && <span className="stat-chip">{logs.length} entries</span>}
          </div>
        </div>

        <AuditFilterForm
          form={form}
          setForm={setForm}
          filtered={filtered}
          onApply={() => setApplied(form)}
          onClear={() => setApplied(EMPTY_FORM)}
        />

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
          <EmptyStateInline
            noun="audit events"
            qualifier={actionFilter || (filtered ? "the applied filters" : undefined)}
          />
        ) : (
          <>
            <DataTable
              columns={columns}
              data={logs}
              getRowKey={(log) => log.id}
              renderExpanded={(log) => (payloadEntries(log.payload).length ? <AuditPayload payload={log.payload} /> : null)}
              exportCsv={{
                name: actionFilter ? `audit-${actionFilter}` : "audit",
                // The table only holds the events loaded so far (see "Load more"), not the whole log.
                note: query.hasNextPage
                  ? `Exports the ${logs.length} loaded events. Load more to include older ones.`
                  : `Exports the ${logs.length} loaded events.`,
              }}
              // Table pagination stays off (it would hide a highlighted row behind a page click); "Load more"
              // is the only paging, so size it past anything the table can hold.
              pageSize={Number.MAX_SAFE_INTEGER}
              getRowRef={(log) => {
                const jobId = parseJobId(log.payload)
                return highlightJobId !== null && jobId === highlightJobId ? highlightRef : undefined
              }}
              rowClassName={(log) => {
                const jobId = parseJobId(log.payload)
                const isHighlighted = highlightJobId !== null && jobId === highlightJobId
                return [
                  "hover:bg-elevated transition-colors",
                  isHighlighted ? "ring-1 ring-inset ring-primary/40 bg-primary/5" : "",
                ].join(" ")
              }}
            />
            {query.hasNextPage && (
              <div className="px-4 py-3 border-t border-border flex justify-center">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={query.isFetchingNextPage}
                  onClick={() => query.fetchNextPage()}
                >
                  {query.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              </div>
            )}
            {query.isError && (
              <p role="alert" className="px-4 pb-3 text-xs text-destructive">
                Couldn&apos;t load more: {query.error.message}
              </p>
            )}
          </>
        )}
      </div>
    </>
  )
}
