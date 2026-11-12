"use client"

import { useState } from "react"
import Link from "next/link"
import { useMutation } from "@tanstack/react-query"
import { CheckCircle, MinusCircle, WarningCircle, XCircle, ArrowSquareOut, Wrench } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ORG_WIDE_ONLY_CHECKS, WAIVABLE_CHECKS, WaiverForm } from "@/components/waiver-form"
import type { CheckResult, CheckValue } from "@/lib/api/types"

// check_ids the API can auto-fix. Keep in sync with check_remediation.supported_check_ids().
const REMEDIABLE_CHECK_IDS = new Set([
  "repository_secret_scanning_enabled",
  "repository_default_branch_protection_enabled",
  "repository_default_branch_no_force_push",
])

interface CheckCardProps {
  check: CheckResult
  // When set, a failing check shows a "File as issue" action for {owner}/{repo}.
  owner?: string
  token?: string
  // Called after a "Fix this" succeeds, so the page can re-scan.
  onRemediated?: () => void
  // When set (the caller is an admin of this Clevis org), a failing check offers "Waive…" for that org.
  waiveOrg?: string
}

// The checks package caps the repos it records per check; keep in sync with github_checks.MAX_AFFECTED_REPOS.
const MAX_AFFECTED_REPOS = 25

/** The repositories a check names, if any. */
function affectedRepos(check: CheckResult): string[] {
  const v = check.value
  return v && (v.type === "ratio" || v.type === "severity_counts") ? (v.repos ?? []) : []
}

const severityLabel: Record<string, string> = {
  high:   "text-red-400",
  medium: "text-yellow-400",
  low:    "text-blue-400",
}

function CheckValueDisplay({ value }: { value: CheckValue }) {
  if (!value) return null

  if (value.type === "text") {
    return (
      <div className="border-t border-border/40 mt-2 pt-2">
        <span className="text-xs text-muted-foreground">{value.text}</span>
      </div>
    )
  }

  if (value.type === "boolean") {
    return (
      <div className="border-t border-border/40 mt-2 pt-2">
        {value.enabled ? (
          <span className="text-xs text-green-400 font-mono">✓ Enabled</span>
        ) : (
          <span className="text-xs text-red-400 font-mono">✗ Disabled</span>
        )}
      </div>
    )
  }

  if (value.type === "severity_counts") {
    const buckets: { key: keyof typeof value; label: string; className: string }[] = [
      { key: "critical", label: "critical", className: "text-red-400 border-red-500/30" },
      { key: "high", label: "high", className: "text-orange-400 border-orange-500/30" },
      { key: "medium", label: "medium", className: "text-yellow-400 border-yellow-500/30" },
      { key: "low", label: "low", className: "text-blue-400 border-blue-500/30" },
    ]
    const nonZero = buckets.filter((b) => (value[b.key] as number) > 0)
    if (nonZero.length === 0) {
      return (
        <div className="border-t border-border/40 mt-2 pt-2">
          <span className="text-xs text-green-400 font-mono">✓ No open alerts</span>
        </div>
      )
    }
    return (
      <div className="border-t border-border/40 mt-2 pt-2 flex flex-wrap gap-1.5">
        {nonZero.map((b) => (
          <span key={b.key} className={`stat-chip ${b.className}`}>
            {value[b.key] as number} {b.label}
          </span>
        ))}
      </div>
    )
  }

  if (value.type === "ratio") {
    const { numerator, denominator } = value
    const pct = denominator === 0 ? 0 : Math.round((numerator / denominator) * 100)
    const barColor =
      pct >= 80 ? "bg-green-400" : pct >= 50 ? "bg-yellow-400" : "bg-red-400"
    const textColor =
      pct >= 80 ? "text-green-400" : pct >= 50 ? "text-yellow-400" : "text-red-400"

    return (
      <div className="border-t border-border/40 mt-2 pt-2">
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${barColor}`}
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className={`text-[0.6875rem] font-mono tabular-nums shrink-0 ${textColor}`}>
            {numerator} / {denominator} · {pct}%{value.unknown ? ` · ${value.unknown} unknown` : ""}
          </span>
        </div>
      </div>
    )
  }

  return null
}

/** Collapsed list of the repos behind a failure, each linking to its detail page. */
function AffectedRepos({ check, owner }: { check: CheckResult; owner?: string }) {
  const repos = affectedRepos(check)
  if (repos.length === 0) return null
  const v = check.value
  // The server keeps only the first MAX_AFFECTED_REPOS names; a ratio tells us whether more failed.
  // A ratio proves whether more failed; for other value shapes a full list only means there may be more.
  const full = repos.length >= MAX_AFFECTED_REPOS
  const capped = full && (v?.type === "ratio" ? v.denominator - v.numerator > repos.length : true)
  const cappedForSure = capped && v?.type === "ratio"
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        {repos.length} {repos.length === 1 ? "repository" : "repositories"} affected
      </summary>
      <ul className="mt-1.5 flex flex-wrap gap-1.5">
        {repos.map((r) => (
          <li key={r}>
            {owner ? (
              <Link href={`/repos/${encodeURIComponent(owner)}~${encodeURIComponent(r)}`} className="stat-chip hover:text-foreground">
                {r}
              </Link>
            ) : (
              <span className="stat-chip">{r}</span>
            )}
          </li>
        ))}
      </ul>
      {capped && (
        <p className="mt-1 text-muted-foreground">Showing the first {repos.length}; {cappedForSure ? "more repositories are affected." : "there may be more."}</p>
      )}
    </details>
  )
}

function FileAsIssue({ check, owner, token }: { check: CheckResult; owner: string; token?: string }) {
  const [open, setOpen] = useState(false)
  // `.github` is GitHub's conventional home for org-wide issues; editable.
  const [repo, setRepo] = useState(".github")
  const [title, setTitle] = useState(check.title)

  const mutation = useMutation({
    mutationFn: () =>
      api.issues.create(
        owner,
        repo.trim(),
        { title: title.trim(), body: `${check.remediation}\n\n_Filed from Clevis._` },
        token,
      ),
  })

  if (mutation.data) {
    return (
      <a
        href={mutation.data.html_url}
        target="_blank"
        rel="noreferrer"
        className="mt-2 inline-flex items-center gap-1 text-xs text-primary hover:underline"
      >
        Issue #{mutation.data.number} created <ArrowSquareOut className="size-3" />
      </a>
    )
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        File as issue
      </button>
    )
  }

  return (
    <div className="mt-2 flex flex-col gap-1.5 border-t border-border/40 pt-2">
      <Input
        value={repo}
        onChange={(e) => setRepo(e.target.value)}
        placeholder="repo (e.g. .github)"
        className="h-7 text-xs"
        aria-label="Repository"
      />
      <Input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Issue title"
        className="h-7 text-xs"
        aria-label="Issue title"
      />
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={mutation.isPending || !repo.trim() || !title.trim()}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? "Creating…" : "Create issue"}
        </Button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          Cancel
        </button>
      </div>
      {mutation.isError && (
        <p className="text-xs text-destructive">
          {mutation.error instanceof Error && /403/.test(mutation.error.message)
            ? "The connected GitHub token needs the 'Issues: write' permission."
            : mutation.error instanceof Error
              ? mutation.error.message
              : "Failed to create the issue."}
        </p>
      )}
    </div>
  )
}

function WaiveThis({ check, orgLogin }: { check: CheckResult; orgLogin: string }) {
  // Prefill the repos the card names, but only for checks where a repo-scoped waiver means something.
  const repos = ORG_WIDE_ONLY_CHECKS.has(check.id) ? [] : affectedRepos(check)
  const [open, setOpen] = useState(false)
  const [saved, setSaved] = useState(false)

  if (saved) {
    return <p className="mt-2 text-xs text-accent">Waiver added. It applies from the next scan.</p>
  }
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 ml-3 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        Waive…
      </button>
    )
  }
  return (
    <div className="mt-2 border-t border-border/40 pt-2">
      <WaiverForm
        orgLogin={orgLogin}
        fixedCheckId={check.id}
        initialRepos={repos}
        compact
        onSaved={() => setSaved(true)}
        onCancel={() => setOpen(false)}
      />
    </div>
  )
}

function FixThisButton({
  check,
  owner,
  token,
  onRemediated,
}: {
  check: CheckResult
  owner: string
  token?: string
  onRemediated?: () => void
}) {
  const [repo, setRepo] = useState("")
  const [armed, setArmed] = useState(false)

  const mutation = useMutation({
    mutationFn: () => api.security.remediate(owner, repo.trim(), check.id, token),
    onSuccess: () => onRemediated?.(),
  })

  if (mutation.isSuccess) {
    return <p className="mt-2 text-xs text-accent">Applied — re-run the scan to confirm.</p>
  }

  return (
    <div className="mt-2 flex flex-col gap-1.5 border-t border-border/40 pt-2">
      <Input
        value={repo}
        onChange={(e) => { setRepo(e.target.value); setArmed(false) }}
        placeholder="repo to fix (e.g. api)"
        className="h-7 text-xs"
        aria-label="Repository to fix"
      />
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          className="h-7 text-xs"
          disabled={mutation.isPending || !repo.trim()}
          onClick={() => (armed ? mutation.mutate() : setArmed(true))}
        >
          <Wrench className="size-3" />
          {mutation.isPending ? "Applying…" : armed ? "Confirm — apply the fix" : "Fix this"}
        </Button>
        {armed && !mutation.isPending && (
          <button
            type="button"
            onClick={() => setArmed(false)}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Cancel
          </button>
        )}
      </div>
      {mutation.isError && (
        <p className="text-xs text-destructive">
          {mutation.error instanceof Error ? mutation.error.message : "The fix could not be applied."}
        </p>
      )}
    </div>
  )
}

export function CheckCard({ check, owner, token, onRemediated, waiveOrg }: CheckCardProps) {
  const pass = check.status === "pass"
  const notApplicable = check.status === "not_applicable"
  const fail = check.status === "fail"
  // Could not be evaluated (e.g. the token or App lacks permission). Counts against the score like a failure,
  // but is not drawn as one, and its numbers are not shown: the API zero-fills them ("No open alerts", "3 / 3").
  const errored = check.status === "error"
  return (
    <div
      className={`bg-card border rounded-md p-3.5 flex items-start gap-3 transition-colors duration-200 ease-(--ease-out) ${
        notApplicable
          ? "border-border/40 hover:border-border/60"
          : pass
            ? "border-accent/20 hover:border-accent/35"
            : errored
              ? "border-yellow-500/25 hover:border-yellow-500/45"
              : "border-destructive/25 hover:border-destructive/45"
      }`}
    >
      {notApplicable ? (
        <MinusCircle className="size-4 shrink-0 mt-0.5 text-muted-foreground" />
      ) : pass ? (
        <CheckCircle className="size-4 shrink-0 mt-0.5 text-accent" />
      ) : errored ? (
        <WarningCircle className="size-4 shrink-0 mt-0.5 text-yellow-400" />
      ) : (
        <XCircle className="size-4 shrink-0 mt-0.5 text-destructive" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className="text-sm font-medium leading-snug">{check.title}</span>
          {notApplicable ? (
            <span className="stat-chip">Not applicable</span>
          ) : (
            <span className={`text-[0.6875rem] font-mono font-medium ${severityLabel[check.severity] ?? "text-muted-foreground"}`}>
              {check.severity}
            </span>
          )}
          {errored && <span className="stat-chip text-yellow-400 border-yellow-500/30">Could not be evaluated</span>}
          {/* Only while the check is excluded from the score: once hygiene scoring is on it counts like any other. */}
          {check.informational && !check.scored && !notApplicable && <span className="stat-chip">Informational</span>}
          {check.waived && (
            <span
              className="stat-chip"
              title={`${check.waived.reason} (owner: ${check.waived.owner}${check.waived.repos ? `; repos: ${check.waived.repos.join(", ")}` : ""})`}
            >
              Waived until {new Date(check.waived.until).toLocaleDateString()}
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">{check.remediation}</p>
        {/* An errored check keeps its explanation, or a ratio that reports how many repos it could not inspect. */}
        {(!errored || check.value?.type === "text" || (check.value?.type === "ratio" && !!check.value.unknown)) && (
          <CheckValueDisplay value={check.value} />
        )}
        {(fail || errored) && <AffectedRepos check={check} owner={owner ?? waiveOrg} />}
        {fail && owner && REMEDIABLE_CHECK_IDS.has(check.id) && (
          <FixThisButton check={check} owner={owner} token={token} onRemediated={onRemediated} />
        )}
        {fail && owner && <FileAsIssue check={check} owner={owner} token={token} />}
        {fail && waiveOrg && check.id in WAIVABLE_CHECKS && <WaiveThis check={check} orgLogin={waiveOrg} />}
      </div>
    </div>
  )
}
