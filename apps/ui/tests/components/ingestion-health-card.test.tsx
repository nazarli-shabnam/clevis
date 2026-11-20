import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const healthMock = vi.fn()
vi.mock("@/lib/api/client", () => ({ api: { ingestionHealth: (...a: unknown[]) => healthMock(...a) } }))

import { IngestionHealthCard } from "@/components/ingestion-health-card"

const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
const health = (over: Record<string, unknown> = {}) => ({
  has_deliveries: true,
  has_webhook_deliveries: true,
  last_received_at: ago(5 * 60_000),
  last_processed_at: ago(6 * 60_000),
  window_hours: 24,
  received: 40,
  queue_failed: 0,
  queue_abandoned: 0,
  dropped: 0,
  pending: 1,
  backlog: 1,
  oldest_backlog_at: ago(2 * 60_000),
  loss_lookback_days: 30,
  lost_recent: 0,
  last_backfill_at: ago(3 * 3_600_000),
  last_membership_reconcile_at: ago(3_600_000),
  ...over,
})

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <IngestionHealthCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => healthMock.mockReset())
afterEach(cleanup)

describe("IngestionHealthCard", () => {
  it("shows a healthy org's times and counts", async () => {
    healthMock.mockResolvedValue(health())
    renderCard()

    expect(await screen.findByText("Webhook deliveries are arriving and being processed.")).toBeInTheDocument()
    expect(healthMock).toHaveBeenCalledWith("acme")
    expect(screen.getByText("5 minutes ago")).toBeInTheDocument()
    expect(screen.getByText(/40 received · 1 waiting · 0 failed to queue · 0 abandoned · 0 dropped/)).toBeInTheDocument()
    expect(screen.getByText("3 hours ago")).toBeInTheDocument()
  })

  it("says history may be incomplete when the last backfill was capped (#1092)", async () => {
    healthMock.mockResolvedValue(health({ backfill_incomplete_reason: "private repositories beyond the 50 most recently pushed were not read" }));
    renderCard();

    expect(await screen.findByText(/History may be incomplete: private repositories beyond the 50/)).toBeInTheDocument();
  });

  it("hints that the webhook may not be configured when nothing has ever arrived", async () => {
    healthMock.mockResolvedValue(
      health({ has_deliveries: false, has_webhook_deliveries: false, last_received_at: null, last_processed_at: null, received: 0, pending: 0, last_backfill_at: null, last_membership_reconcile_at: null }),
    )
    renderCard()

    expect(await screen.findByText(/No webhook delivery has ever arrived/)).toBeInTheDocument()
    expect(screen.getByText(/webhook URL and secret/)).toBeInTheDocument()
    expect(screen.getAllByText("never").length).toBeGreaterThanOrEqual(2)
  })

  it("does not call rollup-only activity a webhook delivery", async () => {
    healthMock.mockResolvedValue(health({ has_webhook_deliveries: false }))
    renderCard()

    expect(await screen.findByText(/no webhook delivery is on record/)).toBeInTheDocument()
    expect(screen.getByText("Last event data")).toBeInTheDocument()
    expect(screen.queryByText("Last webhook received")).not.toBeInTheDocument()
    expect(screen.queryByText("Webhook deliveries are arriving and being processed.")).not.toBeInTheDocument()
  })

  it("warns when deliveries were lost in the window", async () => {
    healthMock.mockResolvedValue(health({ queue_failed: 2, dropped: 1 }))
    renderCard()

    expect(await screen.findByText(/3 webhook deliveries were lost in the last 24 hours/)).toBeInTheDocument()
  })

  it("singular wording for one lost delivery", async () => {
    healthMock.mockResolvedValue(health({ dropped: 1 }))
    renderCard()
    expect(await screen.findByText(/1 webhook delivery was lost/)).toBeInTheDocument()
  })

  it("flags a backlog the worker has not touched for over 15 minutes, even past the 24h window", async () => {
    healthMock.mockResolvedValue(health({ pending: 0, backlog: 4, oldest_backlog_at: ago(3 * 86_400_000) }))
    renderCard()
    expect(await screen.findByText(/4 webhook deliveries have been waiting for the worker since 3 days ago/)).toBeInTheDocument()
  })

  it("singular wording for a single stale delivery", async () => {
    healthMock.mockResolvedValue(health({ backlog: 1, oldest_backlog_at: ago(3_600_000) }))
    renderCard()
    expect(await screen.findByText(/1 webhook delivery has been waiting/)).toBeInTheDocument()
  })

  it("keeps reporting permanent losses for the longer lookback", async () => {
    healthMock.mockResolvedValue(health({ lost_recent: 2 }))
    renderCard()
    expect(await screen.findByText(/2 webhook deliveries were permanently lost in the last 30 days/)).toBeInTheDocument()
  })

  it("keeps reporting a permanent loss next to a stale backlog", async () => {
    healthMock.mockResolvedValue(health({ backlog: 1, oldest_backlog_at: ago(3_600_000), lost_recent: 2 }))
    renderCard()
    const status = await screen.findByRole("status")
    expect(status).toHaveTextContent(/1 webhook delivery has been waiting/)
    expect(status).toHaveTextContent(/2 webhook deliveries were permanently lost in the last 30 days/)
  })

  it("singular wording for one permanent loss", async () => {
    healthMock.mockResolvedValue(health({ lost_recent: 1 }))
    renderCard()
    expect(await screen.findByText(/1 webhook delivery was permanently lost/)).toBeInTheDocument()
  })

  it("shows a load error with a retry", async () => {
    healthMock.mockRejectedValueOnce(new Error("nope")).mockResolvedValue(health())
    renderCard()

    expect(await screen.findByText(/Couldn't load ingestion health: nope/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    expect(await screen.findByText("Webhook deliveries are arriving and being processed.")).toBeInTheDocument()
  })
})
