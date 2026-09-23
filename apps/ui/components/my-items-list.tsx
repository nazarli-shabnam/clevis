import { relativeTime } from "@/lib/format"
import { SectionError } from "@/components/section-error"
import { EmptyStateInline } from "@/components/empty-state"
import { Button } from "@/components/ui/button"
import type { MyViewIssueSummary, MyViewPRSummary } from "@/lib/api/types"

interface MyItemsListProps {
  items: (MyViewPRSummary | MyViewIssueSummary)[]
  isLoading: boolean
  isError: boolean
  errorMessage: string
  onRetry: () => void
  retrying: boolean
  emptyNoun: string
  totalCount: number
  page: number
  perPage: number
  onPageChange: (page: number) => void
  /** Backend couldn't resolve the user's GitHub login; render a distinct message, not "zero items". */
  identityUnresolved?: boolean
}

// Full-page version of the Overview's MyViewRow, with Prev/Next paging.
export function MyItemsList({
  items,
  isLoading,
  isError,
  errorMessage,
  onRetry,
  retrying,
  emptyNoun,
  totalCount,
  page,
  perPage,
  onPageChange,
  identityUnresolved = false,
}: MyItemsListProps) {
  const lastPage = Math.max(1, Math.ceil(totalCount / perPage))

  return (
    <div className="card">
      {isLoading ? (
        <div className="px-4 py-8">
          <p className="text-sm text-muted-foreground animate-pulse">Loading…</p>
        </div>
      ) : isError ? (
        <SectionError message={errorMessage} onRetry={onRetry} retrying={retrying} />
      ) : identityUnresolved ? (
        <div className="px-4 py-8 border-t border-border/60">
          <p className="text-sm text-muted-foreground">
            Clevis can’t tell who you are on GitHub for this account — the connected access
            doesn’t carry a personal identity. Sign in with GitHub (or add a personal access
            token) to see your {emptyNoun} here.
          </p>
        </div>
      ) : items.length === 0 ? (
        <EmptyStateInline noun={emptyNoun} />
      ) : (
        <>
          <div className="divide-y divide-border">
            {items.map((item) => (
              <a
                key={`${item.repository}-${item.number}`}
                href={item.html_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-elevated transition-colors"
              >
                <span className="flex flex-col min-w-0">
                  <span className="text-foreground/90 truncate">{item.title}</span>
                  <span className="text-[0.6875rem] text-muted-foreground font-mono">
                    {item.repository} #{item.number}
                  </span>
                </span>
                <span className="text-[0.6875rem] text-muted-foreground whitespace-nowrap shrink-0 ml-3">
                  {relativeTime(item.updated_at)}
                </span>
              </a>
            ))}
          </div>
          <div className="px-4 py-3 border-t border-border flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              Page {page} of {lastPage} · {totalCount} total
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
                Prev
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={page * perPage >= totalCount}
                onClick={() => onPageChange(page + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
