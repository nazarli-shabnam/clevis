import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const cockpitMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { analytics: { cockpit: (...a: unknown[]) => cockpitMock(...a) } },
}))

import { CompareCard } from "@/components/compare-card"
import type { CockpitResponse } from "@/lib/api/types"

const base = {
  repo_count: 10,
  member_count: 5,
  latest_score: 80,
  score_trend: [70, 80],
  open_pr_count: 4,
  pr_merge_rate_4w: [],
  commit_activity_4w: [1, 2, 10, 20],
  total_cache_size_bytes: 1_000_000,
  release_cadence_4w: [],
  pr_cycle_time_8w: [],
} as unknown as CockpitResponse

function renderCard(memberships: { org_login: string; role: "admin" | "member" }[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={qc}>
      <CompareCard login="acme" cockpit={base} memberships={memberships} />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole("button", { name: /^Compare/ }))
  return view
}

describe("CompareCard", () => {
  beforeEach(() => cockpitMock.mockReset())
  afterEach(cleanup)

  it("compares the last two weeks with the two before by default", () => {
    renderCard([])
    const row = screen.getByRole("row", { name: /^Commits/ })
    expect(within(row).getByText("3")).toBeInTheDocument()
    expect(within(row).getByText("30")).toBeInTheDocument()
    expect(within(row).getByText("+27")).toBeInTheDocument()
  })

  it("compares with another organization the user belongs to", async () => {
    cockpitMock.mockResolvedValue({ ...base, latest_score: 90, open_pr_count: 9 })
    renderCard([
      { org_login: "acme", role: "admin" },
      { org_login: "globex", role: "member" },
    ])

    fireEvent.click(screen.getByRole("button", { name: "Organizations" }))
    expect(screen.queryByRole("option", { name: "acme" })).not.toBeInTheDocument() // not compared with itself
    fireEvent.change(screen.getByLabelText(/Compare acme with/), { target: { value: "globex" } })

    await waitFor(() => expect(cockpitMock).toHaveBeenCalledWith("globex"))
    const score = await screen.findByRole("row", { name: /^Security score/ })
    expect(within(score).getByText("+10")).toBeInTheDocument()
    expect(within(screen.getByRole("row", { name: /^Open pull requests/ })).getByText("+5")).toBeInTheDocument()
  })

  it("says so when there is no other organization", () => {
    renderCard([{ org_login: "acme", role: "admin" }])
    fireEvent.click(screen.getByRole("button", { name: "Organizations" }))
    expect(screen.getByText(/Join or connect another organization/)).toBeInTheDocument()
  })
})
