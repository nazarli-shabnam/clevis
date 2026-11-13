import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const flowMock = vi.fn()
vi.mock("@/lib/api/client", () => ({ api: { repos: { flowMetrics: (...a: unknown[]) => flowMock(...a) } } }))

import { FlowRankingCard } from "@/components/flow-ranking-card"
import { RANKING_REPO_CAP } from "@/lib/flow-ranking"

const metrics = (cycle: number | null, review: number | null, rate: number | null) => ({
  repository: "acme/x",
  window_days: 30,
  prs: { merged_count: 3, median_cycle_hours: cycle, median_first_review_hours: review, review_sample_size: 3, merged_without_review: 0 },
  workflows: rate === null ? [] : [{ name: "CI", runs: 10, failure_rate: rate, avg_duration_seconds: 60, flaky_commits: 0 }],
  workflows_truncated: false,
})

function renderCard(repos: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <FlowRankingCard org="acme" repos={repos} token="" />
    </QueryClientProvider>,
  )
}

const rowOrder = () => screen.getAllByRole("link").map((l) => l.textContent)

beforeEach(() => {
  flowMock.mockReset()
})
afterEach(() => {
  cleanup()
})

describe("FlowRankingCard", () => {
  it("loads nothing until asked, then ranks repos slowest first and links to each", async () => {
    flowMock.mockImplementation((_o: string, _w: string, repo: string) =>
      Promise.resolve(repo === "slow" ? metrics(100, 30, 0.4) : metrics(10, 2, 0)),
    )
    renderCard(["fast", "slow"])
    expect(flowMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole("button", { name: "Rank repositories" }))

    await screen.findByText("40%")
    expect(rowOrder()).toEqual(["slow", "fast"])
    expect(screen.getByRole("link", { name: "slow" })).toHaveAttribute("href", "/repos/acme~slow")
  })

  it("re-sorts when a column header is clicked, and flips the direction on a second click", async () => {
    flowMock.mockImplementation((_o: string, _w: string, repo: string) =>
      Promise.resolve(repo === "a" ? metrics(100, 1, 0.1) : metrics(10, 9, 0.5)),
    )
    renderCard(["a", "b"])
    fireEvent.click(screen.getByRole("button", { name: "Rank repositories" }))
    await screen.findByText("50%")
    expect(rowOrder()).toEqual(["a", "b"]) // cycle time, slowest first

    fireEvent.click(screen.getByRole("button", { name: /Failed runs/ }))
    await waitFor(() => expect(rowOrder()).toEqual(["b", "a"])) // worst failure rate first

    fireEvent.click(screen.getByRole("button", { name: /Failed runs/ }))
    await waitFor(() => expect(rowOrder()).toEqual(["a", "b"]))
  })

  it("covers only the most recently pushed repos and says so", async () => {
    flowMock.mockResolvedValue(metrics(1, 1, 0))
    const repos = Array.from({ length: RANKING_REPO_CAP + 5 }, (_, i) => `r${i}`)
    renderCard(repos)

    fireEvent.click(screen.getByRole("button", { name: "Rank repositories" }))

    await screen.findByText(new RegExp(`Ranking the ${RANKING_REPO_CAP} most recently pushed of ${repos.length}`))
    expect(flowMock).toHaveBeenCalledTimes(RANKING_REPO_CAP)
  })

  it("lists repositories that failed instead of hiding them", async () => {
    flowMock.mockImplementation((_o: string, _w: string, repo: string) =>
      repo === "bad" ? Promise.reject(new Error("boom")) : Promise.resolve(metrics(5, 1, 0)),
    )
    renderCard(["good", "bad"])

    fireEvent.click(screen.getByRole("button", { name: "Rank repositories" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load 1 repository: bad.")
    expect(screen.getByRole("link", { name: "good" })).toBeInTheDocument()
  })

  it("reports an error when no repository could be loaded", async () => {
    flowMock.mockImplementation(() => Promise.reject(new Error("boom")))
    renderCard(["a"])

    fireEvent.click(screen.getByRole("button", { name: "Rank repositories" }))

    expect(await screen.findByText(/Couldn't load flow metrics for any repository \(1 failed\)/)).toBeInTheDocument()
  })
})
