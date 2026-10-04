import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const burndownMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { security: { dependabotBurndown: (...a: unknown[]) => burndownMock(...a) } },
}))
vi.mock("@/components/charts/area-time-chart", () => ({
  AreaTimeChart: ({ data }: { data: unknown[] }) => <div data-testid="chart">{data.length} points</div>,
}))

import DependabotBurndownPage from "@/app/security/burndown/page"
import type { DependabotBurndown } from "@/lib/api/types"

const DATA: DependabotBurndown = {
  window_days: 30,
  severities: [
    { severity: "critical", open: 3, median_age_days: 4, oldest_age_days: 10, sla_days: 7, breaches: 1 },
    { severity: "high", open: 1, median_age_days: 20, oldest_age_days: 20, sla_days: 30, breaches: 0 },
    { severity: "medium", open: 0, median_age_days: null, oldest_age_days: null, sla_days: null, breaches: 0 },
    { severity: "low", open: 2, median_age_days: 90, oldest_age_days: 120, sla_days: null, breaches: 0 },
  ],
  repos: [
    { repo: "acme/api", open: { critical: 3, high: 1, medium: 0, low: 0 }, breaches: 1, oldest_age_days: 10, oldest_alert_number: 42 },
  ],
  trend: [
    { date: "2026-10-03", critical: 2, high: 1, medium: 0, low: 0 },
    { date: "2026-10-04", critical: 3, high: 1, medium: 0, low: 2 },
  ],
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <DependabotBurndownPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  burndownMock.mockReset()
  localStorage.setItem("active_scope", JSON.stringify({ kind: "org", login: "acme" }))
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("DependabotBurndownPage", () => {
  it("shows severity stats, SLA breaches, the trend and a link to the oldest alert", async () => {
    burndownMock.mockResolvedValue(DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText("1 SLA breach")).toBeInTheDocument())
    expect(burndownMock).toHaveBeenCalledWith("acme")
    expect(screen.getByTestId("chart")).toHaveTextContent("2 points")
    expect(screen.getByRole("link", { name: "acme/api" })).toHaveAttribute(
      "href",
      expect.stringContaining("/acme/api/security/dependabot/42"),
    )
    expect(screen.getByText(/1 breaching SLA/)).toBeInTheDocument()
  })

  it("shows an empty state when there are no open alerts", async () => {
    burndownMock.mockResolvedValue({ ...DATA, repos: [] })
    renderPage()

    expect(await screen.findByText("No open Dependabot alerts.")).toBeInTheDocument()
  })

  it("shows the error and does not fetch without an org scope", async () => {
    burndownMock.mockRejectedValue(new Error("Forbidden"))
    renderPage()
    expect(await screen.findByText("Forbidden")).toBeInTheDocument()

    cleanup()
    burndownMock.mockClear()
    localStorage.setItem("active_scope", JSON.stringify({ kind: "personal", login: "me" }))
    renderPage()
    expect(screen.getByText(/Select an organization/)).toBeInTheDocument()
    expect(burndownMock).not.toHaveBeenCalled()
  })
})
