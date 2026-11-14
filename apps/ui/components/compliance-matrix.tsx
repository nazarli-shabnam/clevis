"use client"

import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { DataTable, type DataTableColumn } from "@/components/ui/data-table"
import type { RepoSecurityRow } from "@/lib/api/types"

type Status = "pass" | "fail" | "unknown"

interface Dimension {
  id: string
  label: string
  status: (r: RepoSecurityRow) => Status
  /** What the cell shows. */
  cell: (r: RepoSecurityRow) => React.ReactNode
}

const unknown = (r: RepoSecurityRow, dimension: string) => r.unknown_dimensions.includes(dimension)
const UNKNOWN_TITLE = "unknown — token can't see this"

// The matrix's check columns, in display order. `status` is the one rule for what counts as failing or
// unknown, shared by the cell, the sort, the filter and the CSV.
const DIMENSIONS: Dimension[] = [
  {
    id: "branch_protection",
    label: "Branch",
    status: (r) => (unknown(r, "branch_protection") ? "unknown" : r.branch_protection ? "pass" : "fail"),
    cell: (r) => (unknown(r, "branch_protection") ? "?" : r.branch_protection ? "✓" : "—"),
  },
  {
    id: "secret_scanning",
    label: "Secrets",
    status: (r) => (unknown(r, "secret_scanning") ? "unknown" : r.secret_scanning ? "pass" : "fail"),
    cell: (r) => (r.secret_scanning ? "✓" : "—"),
  },
  {
    id: "dependabot",
    label: "Dependabot",
    status: (r) =>
      unknown(r, "dependabot") ? "unknown" : r.dependabot_critical_count + r.dependabot_high_count > 0 ? "fail" : "pass",
    cell: (r) =>
      unknown(r, "dependabot") ? (
        "?"
      ) : r.dependabot_critical_count + r.dependabot_high_count > 0 ? (
        <span className="text-red-400">{r.dependabot_critical_count + r.dependabot_high_count}</span>
      ) : (
        "✓"
      ),
  },
  {
    id: "code_scanning",
    label: "Code scan",
    status: (r) => (unknown(r, "code_scanning") ? "unknown" : r.code_scanning ? "pass" : "fail"),
    cell: (r) => (unknown(r, "code_scanning") ? "?" : r.code_scanning ? "✓" : "—"),
  },
  {
    id: "force_push",
    label: "Force-push",
    status: (r) => (unknown(r, "force_push") ? "unknown" : r.force_push_allowed ? "fail" : "pass"),
    cell: (r) => (unknown(r, "force_push") ? "?" : r.force_push_allowed ? "✗" : "✓"),
  },
]

// Worst first when ascending, so one click on a column puts the failing repos on top.
const RANK: Record<Status, number> = { fail: 0, unknown: 1, pass: 2 }

/** Whether a repo shows under the "failing or unknown only" toggle for the chosen dimension (any, when none). */
function needsAttention(r: RepoSecurityRow, dimension: string): boolean {
  const dims = dimension === "all" ? DIMENSIONS : DIMENSIONS.filter((d) => d.id === dimension)
  return dims.some((d) => d.status(r) !== "pass")
}

/** The compliance matrix: sortable per check and by score, filterable to the repos that fail (or can't be
 * evaluated for) a check, exportable as CSV, and able to hand the filtered repos to the bulk fixer. */
export function ComplianceMatrix({
  owner,
  repos,
  selectedRepo,
  onSelectRepo,
  onUseInBulkFix,
}: {
  owner: string
  repos: RepoSecurityRow[]
  selectedRepo: string
  onSelectRepo: (repo: string) => void
  /** Set when the caller can run bulk fixes: shows a button handing the filtered repos over. */
  onUseInBulkFix?: (repos: string[]) => void
}) {
  const [dimension, setDimension] = useState("all")
  const [attentionOnly, setAttentionOnly] = useState(false)
  const shown = useMemo(
    () => (attentionOnly ? repos.filter((r) => needsAttention(r, dimension)) : repos),
    [repos, attentionOnly, dimension],
  )

  const columns: DataTableColumn<RepoSecurityRow>[] = [
    {
      key: "repo",
      header: "Repo",
      headerClassName: "text-left px-4",
      cellClassName: "px-4 py-2 font-mono text-foreground/90 truncate max-w-[10rem]",
      sortValue: (r) => r.repo.toLowerCase(),
      // A real button so keyboard and screen-reader users can select a row for the secret-scanning pane.
      render: (r) => (
        <button
          type="button"
          onClick={() => onSelectRepo(r.repo)}
          aria-current={selectedRepo === r.repo ? "true" : undefined}
          className="block max-w-[10rem] truncate text-left font-mono rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          {r.repo}
        </button>
      ),
      csvValue: (r) => r.repo,
    },
    ...DIMENSIONS.map(
      (d): DataTableColumn<RepoSecurityRow> => ({
        key: d.id,
        header: d.label,
        headerClassName: "text-center px-2",
        cellClassName: "text-center px-2 py-2",
        sortValue: (r) => RANK[d.status(r)],
        render: (r) => <span title={d.status(r) === "unknown" ? UNKNOWN_TITLE : undefined}>{d.cell(r)}</span>,
        csvValue: (r) => d.status(r),
      }),
    ),
    {
      key: "score",
      header: "Score",
      align: "right",
      cellClassName: "text-right px-4 py-2 tabular-nums",
      sortValue: (r) => r.score,
      render: (r) => r.score,
      csvValue: (r) => r.score,
    },
  ]

  return (
    <>
      <div className="px-4 py-2 border-b border-border flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-1.5 text-muted-foreground">
          Check
          <select
            aria-label="Check to filter by"
            value={dimension}
            onChange={(e) => setDimension(e.target.value)}
            className="card text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="all">Any check</option>
            {DIMENSIONS.map((d) => (
              <option key={d.id} value={d.id}>{d.label}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-muted-foreground">
          <input type="checkbox" checked={attentionOnly} onChange={(e) => setAttentionOnly(e.target.checked)} />
          Failing or unknown only
        </label>
        <span className="text-muted-foreground" aria-live="polite">
          {attentionOnly ? `${shown.length} of ${repos.length} repositories` : `${repos.length} repositories`}
        </span>
        {onUseInBulkFix && attentionOnly && shown.length > 0 && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => onUseInBulkFix(shown.map((r) => r.repo))}>
            Select these {shown.length} in the bulk fixer
          </Button>
        )}
      </div>
      <DataTable
        columns={columns}
        data={shown}
        getRowKey={(r) => r.repo}
        rowClassName={(r) => `hover:bg-elevated transition-colors ${selectedRepo === r.repo ? "bg-elevated" : ""}`}
        exportCsv={{ name: `security-matrix-${owner}${attentionOnly ? "-filtered" : ""}` }}
      />
    </>
  )
}
