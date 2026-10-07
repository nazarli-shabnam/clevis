"use client"

import { useParams } from "next/navigation"
import { useState } from "react"
import { useInfiniteQuery, useQuery } from "@tanstack/react-query"
import { CircleNotch, DownloadSimple } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { SectionError } from "@/components/section-error"
import { EmptyStateInline } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api/client"
import { toCsv } from "@/lib/csv"
import { downloadTextFile } from "@/lib/download"
import { orgRoleFor } from "@/lib/members-href"
import { EMPTY_FORM, toAuditFilters, type FilterForm } from "@/lib/audit-filters"
import type { AuditLogOut, JobOut, MyOrgMembership } from "@/lib/api/types"

const PAGE_SIZE = 100
const JOBS_PAGE_SIZE = 50

const JOB_STATUS_COLOR: Record<JobOut["status"], string> = {
  queued: "text-muted-foreground",
  processing: "text-yellow-400",
  done: "text-accent",
  failed: "text-destructive",
}

function AuditCard({ orgLogin }: { orgLogin: string }) {
  const [form, setForm] = useState<FilterForm>(EMPTY_FORM)
  // Filters apply on submit, not per keystroke: each change is a server query.
  const [applied, setApplied] = useState<FilterForm>(EMPTY_FORM)

  const query = useInfiniteQuery({
    queryKey: ["org-audit", orgLogin, applied],
    queryFn: ({ pageParam }) =>
      api.audit.listForOrg(orgLogin, { ...toAuditFilters(applied), before_id: pageParam, limit: PAGE_SIZE }),
    initialPageParam: undefined as number | undefined,
    // A full page means there may be older rows; the next page starts below the last id seen.
    getNextPageParam: (last) => (last.length === PAGE_SIZE ? last[last.length - 1].id : undefined),
  })
  const rows: AuditLogOut[] = query.data?.pages.flat() ?? []
  const filtered = JSON.stringify(applied) !== JSON.stringify(EMPTY_FORM)

  function exportCsv() {
    const csv = toCsv(rows, [
      { header: "Time", value: (r) => r.created_at },
      { header: "Actor", value: (r) => r.actor },
      { header: "Action", value: (r) => r.action },
      { header: "Target", value: (r) => r.target },
    ])
    downloadTextFile(`clevis-activity-${orgLogin}-${new Date().toISOString().slice(0, 10)}.csv`, csv, "text/csv")
  }

  const field = (id: string, label: string, key: keyof FilterForm, props: { type?: string; placeholder?: string } = {}) => (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-foreground">{label}</label>
      <Input
        id={id}
        value={form[key]}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
        className="h-8 text-xs w-40"
        {...props}
      />
    </div>
  )

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <span className="section-title">Audit log</span>
        <Button size="sm" variant="outline" onClick={exportCsv} disabled={rows.length === 0}>
          <DownloadSimple className="size-3.5" /> {query.hasNextPage ? `Export CSV (${rows.length} loaded)` : "Export CSV"}
        </Button>
      </div>

      <form
        className="px-4 py-3 border-b border-border flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          setApplied(form)
        }}
      >
        {field("act-prefix", "Action starts with", "actionPrefix", { placeholder: "e.g. token." })}
        {field("act-actor", "Actor", "actor", { placeholder: "email" })}
        {field("act-target", "Target contains", "target", { placeholder: "e.g. acme/api" })}
        {field("act-from", "From (UTC)", "from", { type: "date" })}
        {field("act-to", "To (UTC)", "to", { type: "date" })}
        <Button type="submit" size="sm">Apply filters</Button>
        {filtered && (
          <button
            type="button"
            className="text-xs text-muted-foreground underline hover:text-foreground"
            onClick={() => {
              setForm(EMPTY_FORM)
              setApplied(EMPTY_FORM)
            }}
          >
            Clear
          </button>
        )}
      </form>

      {query.isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError && !query.data ? (
        <SectionError
          message={`Couldn't load the audit log: ${query.error.message}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : rows.length === 0 ? (
        <EmptyStateInline noun="audit events" qualifier={filtered ? "matching these filters" : undefined} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Time</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Actor</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Action</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Target</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((r) => (
                  <tr key={r.id} className="hover:bg-elevated transition-colors">
                    <td className="px-4 py-2 font-mono text-muted-foreground whitespace-nowrap">{new Date(r.created_at).toLocaleString()}</td>
                    <td className="px-4 py-2 font-mono text-foreground/80">{r.actor}</td>
                    <td className="px-4 py-2 font-mono text-primary">{r.action}</td>
                    <td className="px-4 py-2 text-muted-foreground max-w-[16rem] truncate" title={r.target}>{r.target}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-4 py-3 border-t border-border flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">{rows.length} loaded{query.hasNextPage ? " — older events available" : ""}</span>
            {query.hasNextPage && (
              <Button size="sm" variant="outline" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
                {query.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            )}
          </div>
          {query.isError && (
            <p role="alert" className="px-4 pb-3 text-xs text-destructive">Couldn&apos;t load more: {query.error.message}</p>
          )}
        </>
      )}
    </div>
  )
}

function JobsCard({ orgLogin }: { orgLogin: string }) {
  const query = useInfiniteQuery({
    queryKey: ["org-jobs", orgLogin],
    queryFn: ({ pageParam }) => api.jobs.listForOrg(orgLogin, { before_id: pageParam, limit: JOBS_PAGE_SIZE }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.length === JOBS_PAGE_SIZE ? last[last.length - 1].id : undefined),
  })
  const jobs: JobOut[] = query.data?.pages.flat() ?? []

  return (
    <div className="card mt-4">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3">
        <span className="section-title">Background jobs</span>
        <Button size="sm" variant="outline" onClick={() => query.refetch()} disabled={query.isFetching}>
          Refresh
        </Button>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError && !query.data ? (
        <SectionError
          message={`Couldn't load jobs: ${query.error.message}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : jobs.length === 0 ? (
        <EmptyStateInline noun="background jobs" />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Job</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Type</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Status</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Result</th>
                  <th className="text-right text-muted-foreground font-medium px-4 py-2">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {jobs.map((j) => (
                  <tr key={j.id} className="hover:bg-elevated transition-colors">
                    <td className="px-4 py-2 font-mono text-muted-foreground">#{j.id}</td>
                    <td className="px-4 py-2 font-mono text-foreground/80">{j.job_type}</td>
                    <td className={`px-4 py-2 font-medium ${JOB_STATUS_COLOR[j.status]}`}>{j.status}</td>
                    <td className="px-4 py-2 text-muted-foreground max-w-[18rem] truncate" title={j.result ?? undefined}>{j.result ?? "—"}</td>
                    <td className="px-4 py-2 text-right font-mono text-muted-foreground whitespace-nowrap">{new Date(j.updated_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {query.hasNextPage && (
            <div className="px-4 py-3 border-t border-border flex justify-center">
              <Button size="sm" variant="outline" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
                {query.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function OrgActivityPage() {
  const params = useParams<{ login: string }>()
  const orgLogin = params.login

  // Both lists are admin-only on the API; look up the caller's role so a plain member gets an
  // explanation, not two panels that can only 403. If the lookup itself fails, don't guess: show the
  // panels and let the API's verdict surface through their own error states.
  const membershipsQuery = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })
  const role = membershipsQuery.data ? orgRoleFor(membershipsQuery.data, orgLogin) : undefined
  const notAdmin = role !== undefined && role !== "admin"

  return (
    <>
      <PageHeader title="Activity log" description={`Who did what in ${orgLogin}, and how its background jobs ran.`} />

      {membershipsQuery.isLoading ? (
        <div className="card px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Checking your access…
        </div>
      ) : notAdmin ? (
        <div className="card px-4 py-4">
          <span className="section-title">Activity log</span>
          <p className="text-xs text-muted-foreground mt-1">
            {role === "member"
              ? `The activity log for ${orgLogin} is limited to organization admins. You're a member, not an admin.`
              : `The activity log for ${orgLogin} needs an admin membership, and you don't have one for this organization.`}
          </p>
        </div>
      ) : (
        <>
          <AuditCard orgLogin={orgLogin} />
          <JobsCard orgLogin={orgLogin} />
        </>
      )}
    </>
  )
}
