"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { relativeTime } from "@/lib/format"
import { SectionError } from "@/components/section-error"
import type { MyViewPRSummary, MyViewResponse } from "@/lib/api/types"

const MAX_ROWS = 5

// Failing first: those are what the developer has to act on.
const CI_ORDER = { failing: 0, pending: 1, unknown: 2, passing: 3 } as const
const CI_LABEL = { failing: "CI failing", pending: "CI running", unknown: "CI unknown", passing: "CI passing" } as const
const CI_CLASS = {
  failing: "text-destructive",
  pending: "text-warning",
  unknown: "text-muted-foreground",
  passing: "text-green-400",
} as const

/** The user's open PRs ordered by CI state (failing first), newest activity first within a state. */
export function sortByCiStatus(prs: MyViewPRSummary[]): MyViewPRSummary[] {
  const rank = (p: MyViewPRSummary) => CI_ORDER[p.ci_status ?? "unknown"]
  return [...prs].sort((a, b) => rank(a) - rank(b) || b.updated_at.localeCompare(a.updated_at))
}

function InboxSection({
  title,
  empty,
  children,
  count,
  viewAllHref,
}: {
  title: string
  empty: string
  children: React.ReactNode
  count: number
  viewAllHref: string
}) {
  return (
    <section className="card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">{title}</span>
        <span className="stat-chip">{count}</span>
      </div>
      {count === 0 ? (
        <p className="px-4 py-4 text-sm text-muted-foreground">{empty}</p>
      ) : (
        <>
          <div className="divide-y divide-border">{children}</div>
          <Link href={viewAllHref} className="block px-4 py-2 text-xs text-muted-foreground hover:text-foreground border-t border-border">
            View all {count} →
          </Link>
        </>
      )}
    </section>
  )
}

const rowClass = "flex items-center justify-between gap-3 px-4 py-2.5 text-sm hover:bg-elevated transition-colors"

function PrRow({ pr, trailing }: { pr: MyViewPRSummary; trailing: React.ReactNode }) {
  return (
    <a href={pr.html_url} target="_blank" rel="noreferrer" className={rowClass}>
      <span className="flex flex-col min-w-0">
        <span className="text-foreground/90 truncate">{pr.title}</span>
        <span className="text-[0.6875rem] text-muted-foreground font-mono">{pr.repository} #{pr.number}</span>
      </span>
      {trailing}
    </a>
  )
}

/** Summary above My Work's tabs: reviews waiting on you, your open PRs with their CI state, and your assigned issues. */
export function DeveloperInbox({ org, token }: { org: string; token?: string }) {
  const { data, isError, error, refetch, isFetching } = useQuery<MyViewResponse>({
    queryKey: ["analytics.my-view", org],
    queryFn: () => api.analytics.myView(org, token),
    retry: false,
  })

  if (isError) {
    return (
      <div className="card mb-6">
        <SectionError
          message={error instanceof Error ? error.message : "Failed to load your inbox."}
          onRetry={() => refetch()}
          retrying={isFetching}
        />
      </div>
    )
  }

  // Hidden while loading or when GitHub can't tell who the user is: the tabs below already explain
  // that, and an inbox full of false "nothing waiting" would mislead.
  if (!data || data.identity_unresolved) return null

  const reviews = [...data.review_requests]
    .sort((a, b) => (a.created_at ?? a.updated_at).localeCompare(b.created_at ?? b.updated_at))
    .slice(0, MAX_ROWS)
  const myPrs = sortByCiStatus(data.my_open_prs).slice(0, MAX_ROWS)

  return (
    <div className="mb-6">
      {data.incomplete && (
        <p role="alert" className="mb-2 text-xs text-destructive">
          Some GitHub searches failed, so these lists and counts may be incomplete.
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-3">
        <InboxSection title="Reviews waiting on you" empty="No reviews waiting." count={data.review_requests_total} viewAllHref="/my?tab=reviews">
          {reviews.map((pr) => (
            <PrRow
              key={`${pr.repository}-${pr.number}`}
              pr={pr}
              trailing={
                <span
                  className="text-[0.6875rem] text-warning whitespace-nowrap shrink-0"
                  title="When the pull request was opened. GitHub doesn't expose when your review was requested."
                >
                  PR opened {relativeTime(pr.created_at ?? pr.updated_at)}
                </span>
              }
            />
          ))}
        </InboxSection>
        <InboxSection title="Your open PRs" empty="No open pull requests." count={data.my_open_prs_total} viewAllHref="/my">
          {myPrs.map((pr) => {
            const ci = pr.ci_status ?? "unknown"
            return (
              <PrRow
                key={`${pr.repository}-${pr.number}`}
                pr={pr}
                trailing={<span className={`text-[0.6875rem] whitespace-nowrap shrink-0 ${CI_CLASS[ci]}`}>{CI_LABEL[ci]}</span>}
              />
            )
          })}
        </InboxSection>
        <InboxSection title="Assigned to you" empty="No assigned issues." count={data.assigned_issues_total} viewAllHref="/my?tab=issues">
          {data.assigned_issues.slice(0, MAX_ROWS).map((issue) => (
            <a key={`${issue.repository}-${issue.number}`} href={issue.html_url} target="_blank" rel="noreferrer" className={rowClass}>
              <span className="flex flex-col min-w-0">
                <span className="text-foreground/90 truncate">{issue.title}</span>
                <span className="text-[0.6875rem] text-muted-foreground font-mono">{issue.repository} #{issue.number}</span>
              </span>
              <span className="text-[0.6875rem] text-muted-foreground whitespace-nowrap shrink-0">{relativeTime(issue.updated_at)}</span>
            </a>
          ))}
        </InboxSection>
      </div>
    </div>
  )
}
