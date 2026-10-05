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
  prs: { merged_count: 12, median_cycle_hours: 26.5, median_first_review_hours: 4, review_sample_size: 12, merged_without_review: 3, review_lookup_failed: 0 },
  workflows: [
    { name: "CI", runs: 40, failure_rate: 0.2, avg_duration_seconds: 600, flaky_commits: 2 },
    { name: "Lint", runs: 40, failure_rate: 0, avg_duration_seconds: 30, flaky_commits: 0 },
  ],
  workflows_truncated: false,
  prs_truncated: false,
}

function renderCard(props: Partial<React.ComponentProps<typeof FlowMetricsCard>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (extra: typeof props) => (
    <QueryClientProvider client={queryClient}>
      <FlowMetricsCard org="acme" owner="acme" repo="api" token="" {...props} {...extra} />
    </QueryClientProvider>
  )
  const utils = render(tree({}))
  return { ...utils, update: (extra: typeof props) => utils.rerender(tree(extra)) }
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
    [47.6, "2d"], // the remainder rounds up to 24h: carry into the day count, not "1d 24h"
    [47.4, "1d 23h"],
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

  it("ranks flaky workflows by flaky commits and says so when there is nothing to flag", async () => {
    flowMock.mockResolvedValue({
      ...DATA,
      workflows: [
        { name: "A", runs: 5, failure_rate: 0.1, avg_duration_seconds: null, flaky_commits: 1 },
        { name: "B", runs: 5, failure_rate: 0.1, avg_duration_seconds: null, flaky_commits: 3 },
      ],
    })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText("3 commits")).toBeInTheDocument()
    expect(screen.getByText("1 commit")).toBeInTheDocument()
    expect(screen.getAllByText("B")[0].compareDocumentPosition(screen.getAllByText("A")[0])).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(screen.getByText("Nothing to flag.")).toBeInTheDocument()
  })

  it("shows the error message when loading fails", async () => {
    flowMock.mockRejectedValue(new Error("GitHub API error"))
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    await waitFor(() => expect(screen.getByText("GitHub API error")).toBeInTheDocument())
  })

  it("offers a Retry after an error and reloads on click", async () => {
    flowMock.mockRejectedValueOnce(new Error("GitHub API error")).mockResolvedValueOnce(DATA)
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText("GitHub API error")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))

    expect(await screen.findByText("Median cycle time:")).toBeInTheDocument()
    expect(flowMock).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
  })

  it("waits for the token to resolve instead of sending an empty one", async () => {
    flowMock.mockResolvedValue(DATA)
    const { update } = renderCard({ token: "", tokenReady: false })
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    await new Promise((r) => setTimeout(r, 20))
    expect(flowMock).not.toHaveBeenCalled()

    update({ token: "ghp_resolved", tokenReady: true })
    await waitFor(() => expect(flowMock).toHaveBeenCalledWith("acme", "acme", "api", "ghp_resolved"))
    expect(flowMock).toHaveBeenCalledTimes(1)
  })

  it("separates reviews that could not be loaded from PRs that had no review", async () => {
    flowMock.mockResolvedValue({
      ...DATA,
      prs: { ...DATA.prs, review_sample_size: 12, merged_without_review: 2, review_lookup_failed: 4 },
    })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText("2 of the last 8 merged had no review.")).toBeInTheDocument()
    expect(screen.getByText(/Reviews for 4 merged PRs could not be loaded/)).toBeInTheDocument()
  })

  it("renders figures from an API that predates the new PR fields", async () => {
    const { review_lookup_failed: _a, ...prs } = DATA.prs
    const { prs_truncated: _b, ...legacy } = DATA
    flowMock.mockResolvedValue({ ...legacy, prs })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText("3 of the last 12 merged had no review.")).toBeInTheDocument()
  })

  it("notes when PR figures are truncated", async () => {
    flowMock.mockResolvedValue({ ...DATA, prs_truncated: true })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText(/PR figures cover only the most recent ones/)).toBeInTheDocument()
  })

  it("notes when workflow figures are truncated", async () => {
    flowMock.mockResolvedValue({ ...DATA, workflows_truncated: true })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Load metrics" }))

    expect(await screen.findByText(/cover only the most recent ones/)).toBeInTheDocument()
  })
})
