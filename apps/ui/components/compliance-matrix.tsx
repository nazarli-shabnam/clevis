"use client"

import { useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { DataTable, type DataTableColumn } from "@/components/ui/data-table"
import type { ControlMapping, RepoSecurityRow } from "@/lib/api/types"

type Status = "pass" | "fail" | "unknown"

interface Dimension {
  id: string
  label: string
  /** The scan check this column is the per-repo view of, for the control mapping. */
  checkId: string
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
    checkId: "repository_default_branch_protection_enabled",
    label: "Branch",
    status: (r) => (unknown(r, "branch_protection") ? "unknown" : r.branch_protection ? "pass" : "fail"),
    cell: (r) => (unknown(r, "branch_protection") ? "?" : r.branch_protection ? "✓" : "—"),
  },
  {
    id: "secret_scanning",
    checkId: "repository_secret_scanning_enabled",
    label: "Secrets",
    status: (r) => (unknown(r, "secret_scanning") ? "unknown" : r.secret_scanning ? "pass" : "fail"),
    cell: (r) => (unknown(r, "secret_scanning") ? "?" : r.secret_scanning ? "✓" : "—"),
  },
  {
    id: "dependabot",
    checkId: "repository_dependabot_alerts_clear",
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
    checkId: "repository_code_scanning_alerts_clear",
    label: "Code scan",
    status: (r) => (unknown(r, "code_scanning") ? "unknown" : r.code_scanning ? "pass" : "fail"),
    cell: (r) => (unknown(r, "code_scanning") ? "?" : r.code_scanning ? "✓" : "—"),
  },
  {
    id: "force_push",
    checkId: "repository_default_branch_no_force_push",
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

/** The controls of the chosen framework that a repo's failing or unknown checks put at risk (each once, in the
 * order the columns list them; only the filtered check's when the table is filtered to one). Unknown counts: an unverifiable check is no evidence for its controls either. */
function controlsAtRisk(r: RepoSecurityRow, controls: ControlMapping, framework: string, dimension = "all") {
  const seen = new Map<string, string>()
  for (const d of dimension === "all" ? DIMENSIONS : DIMENSIONS.filter((x) => x.id === dimension)) {
    if (d.status(r) === "pass") continue
    for (const c of controls.checks[d.checkId] ?? []) {
      if (c.framework === framework && !seen.has(c.id)) seen.set(c.id, c.name)
    }
  }
  return [...seen].map(([id, name]) => ({ id, name }))
}

/** The compliance matrix: sortable per check and by score, filterable to the repos that fail (or can't be
 * evaluated for) a check, exportable as CSV, and able to hand the filtered repos to the bulk fixer. */
export function ComplianceMatrix({
  owner,
  repos,
  selectedRepo,
  onSelectRepo,
  onUseInBulkFix,
  controls,
}: {
  owner: string
  repos: RepoSecurityRow[]
  selectedRepo: string
  onSelectRepo: (repo: string) => void
  /** Set when the caller can run bulk fixes: shows a button handing the filtered repos over. */
  onUseInBulkFix?: (repos: string[], viaKeyboard: boolean) => void
  /** The check-to-control table; when set, a column shows the controls each repo's failing checks put at risk. */
  controls?: ControlMapping
}) {
  const [dimension, setDimension] = useState("all")
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [chosenFramework, setFramework] = useState("soc2")
  const framework = controls?.frameworks.some((f) => f.id === chosenFramework) ? chosenFramework : (controls?.frameworks[0]?.id ?? chosenFramework)
  const shown = useMemo(
    () => (attentionOnly ? repos.filter((r) => needsAttention(r, dimension)) : repos),
    [repos, attentionOnly, dimension],
  )

  // The controls column sorts by values that depend on the framework and the check filter, which DataTable's
  // sort memo cannot see; a new array when they change makes it sort again.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const sortable = useMemo(() => [...shown], [shown, framework, dimension])

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
    ...(controls
      ? [
          {
            key: "controls",
            header: "Controls at risk",
            headerClassName: "text-left px-2",
            cellClassName: "px-2 py-2 text-muted-foreground",
            sortValue: (r: RepoSecurityRow) => -controlsAtRisk(r, controls, framework, dimension).length,
            render: (r: RepoSecurityRow) => {
              const at = controlsAtRisk(r, controls, framework, dimension)
              return at.length === 0 ? (
                "—"
              ) : (
                <span title={at.map((c) => `${c.id} ${c.name}`).join("\n")}>{at.map((c) => c.id).join(", ")}</span>
              )
            },
            csvValue: (r: RepoSecurityRow) => controlsAtRisk(r, controls, framework, dimension).map((c) => c.id).join("; "),
          } satisfies DataTableColumn<RepoSecurityRow>,
        ]
      : []),
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
        {controls && (
          <label className="flex items-center gap-1.5 text-muted-foreground">
            Controls
            <select
              aria-label="Framework for the controls column"
              value={framework}
              onChange={(e) => setFramework(e.target.value)}
              className="card text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
            >
              {controls.frameworks.map((f) => (
                <option key={f.id} value={f.id}>{`${f.name} (${f.version})`}</option>
              ))}
            </select>
          </label>
        )}
        <label className="flex items-center gap-1.5 text-muted-foreground">
          <input type="checkbox" checked={attentionOnly} onChange={(e) => setAttentionOnly(e.target.checked)} />
          Failing or unknown only
        </label>
        <span className="text-muted-foreground" aria-live="polite">
          {attentionOnly ? `${shown.length} of ${repos.length} repositories` : `${repos.length} repositories`}
        </span>
        {onUseInBulkFix && attentionOnly && shown.length > 0 && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={(e) => onUseInBulkFix(shown.map((r) => r.repo), e.detail === 0)}>
            Select these {shown.length} in the bulk fixer
          </Button>
        )}
      </div>
      <DataTable
        columns={columns}
        data={sortable}
        getRowKey={(r) => r.repo}
        rowClassName={(r) => `hover:bg-elevated transition-colors ${selectedRepo === r.repo ? "bg-elevated" : ""}`}
        exportCsv={{ name: `security-matrix-${owner}${attentionOnly ? "-filtered" : ""}` }}
      />
    </>
  )
}
