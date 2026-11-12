import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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
  window.history.replaceState(null, "", "/")
})

describe("DependabotBurndownPage", () => {
  it("shows severity stats, SLA breaches, the trend and a link to the oldest alert", async () => {
    burndownMock.mockResolvedValue(DATA)
    renderPage()

    await waitFor(() => expect(screen.getByText("1 SLA breach")).toBeInTheDocument())
    expect(burndownMock).toHaveBeenCalledWith("acme", 30)
    expect(screen.getByTestId("chart")).toHaveTextContent("2 points")
    expect(screen.getByRole("link", { name: "acme/api" })).toHaveAttribute("href", "/repos/acme~api?tab=security")
    expect(screen.getByRole("link", { name: "Open the oldest alert of acme/api on GitHub" })).toHaveAttribute(
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

  it("retries a failed load", async () => {
    burndownMock.mockRejectedValueOnce(new Error("Forbidden")).mockResolvedValue(DATA)
    renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "Retry" }))

    expect(await screen.findByText("1 SLA breach")).toBeInTheDocument()
  })

  it("pluralises the breach count", async () => {
    burndownMock.mockResolvedValue({
      ...DATA,
      severities: DATA.severities.map((s) => ({ ...s, breaches: 0 })),
    })
    renderPage()

    expect(await screen.findByText("0 SLA breaches")).toBeInTheDocument()
  })

  it("refetches for the chosen window and keeps it in the URL", async () => {
    burndownMock.mockResolvedValue(DATA)
    renderPage()
    await screen.findByText("1 SLA breach")

    fireEvent.change(screen.getByLabelText("Window"), { target: { value: "90" } })

    await waitFor(() => expect(burndownMock).toHaveBeenLastCalledWith("acme", 90))
    expect(window.location.search).toBe("?days=90")
  })

  it("restores the view from the URL and narrows to a deep-linked repo", async () => {
    window.history.replaceState(null, "", "/security/burndown?days=60&repo=acme%2Fweb")
    burndownMock.mockResolvedValue({
      ...DATA,
      repos: [
        ...DATA.repos,
        { repo: "acme/web", open: { critical: 0, high: 2 }, breaches: 0, oldest_age_days: 5, oldest_alert_number: 7 },
      ],
    })
    renderPage()

    expect(await screen.findByRole("link", { name: "acme/web" })).toBeInTheDocument()
    expect(burndownMock).toHaveBeenCalledWith("acme", 60)
    expect(screen.queryByRole("link", { name: "acme/api" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByTitle("Show all repositories"))
    expect(await screen.findByRole("link", { name: "acme/api" })).toBeInTheDocument()
  })

  it("filters by severity and sorts from the controls, and says when nothing matches", async () => {
    burndownMock.mockResolvedValue({
      ...DATA,
      repos: [
        ...DATA.repos,
        { repo: "acme/web", open: { critical: 0, high: 2 }, breaches: 0, oldest_age_days: 50, oldest_alert_number: 7 },
        { repo: "weird", open: { critical: 0, high: 1 }, breaches: 0, oldest_age_days: 1, oldest_alert_number: 8 },
      ],
    })
    renderPage()
    await screen.findByRole("link", { name: "acme/api" })
    // A repo name that isn't owner/name has no in-app page, so it renders as plain text.
    expect(screen.queryByRole("link", { name: "weird" })).not.toBeInTheDocument()
    expect(screen.getByText("weird")).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "breaches" } })
    await waitFor(() => expect(window.location.search).toContain("sort=breaches"))

    fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "critical" } })
    await waitFor(() => expect(screen.queryByRole("link", { name: "acme/web" })).not.toBeInTheDocument())
    expect(screen.getByRole("link", { name: "acme/api" })).toBeInTheDocument()
    expect(window.location.search).toContain("severity=critical")
  })

  it("explains an empty result caused by the filters", async () => {
    burndownMock.mockResolvedValue({
      ...DATA,
      repos: [{ repo: "acme/web", open: { critical: 0, high: 2 }, breaches: 0, oldest_age_days: 5, oldest_alert_number: 7 }],
    })
    renderPage()
    await screen.findByRole("link", { name: "acme/web" })

    fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "critical" } })

    expect(await screen.findByText("No repositories match these filters.")).toBeInTheDocument()
  })
})
