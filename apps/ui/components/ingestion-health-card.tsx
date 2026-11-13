"use client"

import { useQuery } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { relativeTime } from "@/lib/format"
import { SectionError } from "@/components/section-error"
import type { IngestionHealth } from "@/lib/api/types"

const when = (iso: string | null) => (iso ? relativeTime(iso) : "never")

/** What is wrong with ingestion, if anything, in one sentence; null when it looks healthy. */
export function ingestionProblem(h: IngestionHealth): string | null {
  if (!h.has_deliveries) {
    return "No webhook delivery has ever arrived for this organization. Check that the GitHub App's webhook URL and secret are configured (see docs/self-hosting.md)."
  }
  if (!h.has_webhook_deliveries) {
    return "Event data is present, but no webhook delivery is on record (it may come from backfill, or old deliveries were purged). Check that the GitHub App's webhook URL and secret are configured (see docs/self-hosting.md)."
  }
  const lost = h.queue_failed + h.queue_abandoned + h.dropped
  if (lost > 0) {
    return `${lost} webhook deliver${lost === 1 ? "y was" : "ies were"} lost in the last ${h.window_hours} hours, so some activity and alert data may be missing.`
  }
  return null
}

/** Is GitHub data reaching Clevis? Lets an admin tell a quiet org from broken ingestion when a
 * webhook-backed view says "No events recorded". */
export function IngestionHealthCard({ orgLogin }: { orgLogin: string }) {
  const query = useQuery<IngestionHealth>({
    queryKey: ["ingestion-health", orgLogin],
    queryFn: () => api.ingestionHealth(orgLogin),
    retry: false,
  })
  const h = query.data
  const problem = h ? ingestionProblem(h) : null

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Ingestion health</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !h ? (
        <SectionError
          message={`Couldn't load ingestion health: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-3 text-xs">
          <p
            role="status"
            className={problem ? "text-warning" : "text-muted-foreground"}
          >
            {problem ?? "Webhook deliveries are arriving and being processed."}
          </p>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
            <dt className="text-muted-foreground">{h.has_webhook_deliveries ? "Last webhook received" : "Last event data"}</dt>
            <dd>{when(h.last_received_at)}</dd>
            <dt className="text-muted-foreground">Last webhook processed</dt>
            <dd>{when(h.last_processed_at)}</dd>
            <dt className="text-muted-foreground">Last {h.window_hours} hours</dt>
            <dd>
              {h.received} received · {h.pending} waiting · {h.queue_failed} failed to queue · {h.queue_abandoned} abandoned · {h.dropped} dropped
            </dd>
            <dt className="text-muted-foreground">Last event backfill</dt>
            <dd>{when(h.last_backfill_at)}</dd>
            <dt className="text-muted-foreground">Last membership reconcile</dt>
            <dd>{when(h.last_membership_reconcile_at)}</dd>
          </dl>
        </div>
      )}
    </div>
  )
}
