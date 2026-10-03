import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const flowMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { repos: { flowMetrics: (...a: unknown[]) => flowMock(...a) } },
}))

import { FlowMetricsCard, formatHours } from "@/components/flow-metrics-card"
import type { RepoFlowMetricsResponse } from "@/lib/api/types"

const DATA: RepoFlowMetricsResponse = {
  repository: "acme/api",
  window_days: 30,
  prs: { merged_count: 12, median_cycle_hours: 26.5, median_first_review_hours: 4, review_sample_size: 12, merged_without_review: 3 },
  workflows: [
    { name: "CI", runs: 40, failure_rate: 0.2, avg_duration_seconds: 600, flaky_commits: 2 },
    { name: "Lint", runs: 40, failure_rate: 0, avg_duration_seconds: 30, flaky_commits: 0 },
  ],
  workflows_truncated: false,
}

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <FlowMetricsCard org="acme" owner="acme" repo="api" token="" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  flowMock.mockReset()
})
afterEach(cleanup)

describe("formatHours", () => {
  it.each([
    [null, "—"],
    [4, "4h"],
    [26.5, "1d 3h"],
    [48, "2d"],
  ])("formats %s as %s", (input, expected) => {
    expect(formatHours(input)).toBe(expected)
  })
})

describe("FlowMetricsCard", () => {
  it("does not call the API until the user asks for the metrics", () => {
    renderCard()
    expect(flowMock).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Load metrics" })).toBeInTheDocument()
  })

  it("shows PR figures plus slowest and flaky workflows after loading", async () => {
    flowMock.mockResolvedValue(DATA)
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    await waitFor(() => expect(screen.getByText("Median cycle time:")).toBeInTheDocument())
    expect(flowMock).toHaveBeenCalledWith("acme", "acme", "api", "")
    expect(screen.getByText("1d 3h")).toBeInTheDocument()
    expect(screen.getByText("3 of the last 12 merged had no review.")).toBeInTheDocument()
    expect(screen.getByText("10m avg")).toBeInTheDocument()
    expect(screen.getByText("2 commits")).toBeInTheDocument()
  })

  it("shows the error message when loading fails", async () => {
    flowMock.mockRejectedValue(new Error("GitHub API error"))
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    await waitFor(() => expect(screen.getByText("GitHub API error")).toBeInTheDocument())
  })

  it("notes when workflow figures are truncated", async () => {
    flowMock.mockResolvedValue({ ...DATA, workflows_truncated: true })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText(/cover only the most recent ones/)).toBeInTheDocument()
  })
})
