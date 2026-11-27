import { cleanup, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const exportMock = vi.fn()
vi.mock("@/lib/api/client", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/client")>()),
  api: { analytics: { exportHistory: (...a: unknown[]) => exportMock(...a) } },
}))

import { RepoCheckHistoryCard } from "@/components/repo-check-history"

// The stored shape of the branch-protection check value, as the export returns it.
const ratio = (repos?: string[]) => ({ checked: 3, protected: 2, unknown: 0, repos })
const scan = (id: number, day: number, status: string, repos?: string[]) => ({
  id,
  owner: "acme",
  score: 80,
  total_checks: 1,
  failed_checks: status === "fail" ? 1 : 0,
  created_at: `2026-11-${String(day).padStart(2, "0")}T10:00:00Z`,
  checks: [{ id: "repository_default_branch_protection_enabled", title: "Branch protection", severity: "high", status, value: ratio(repos) }],
})

function wrap(enabled = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <RepoCheckHistoryCard owner="acme" repo="legacy" enabled={enabled} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  exportMock.mockReset()
})
afterEach(cleanup)

describe("RepoCheckHistoryCard", () => {
  it("shows when a repo started failing, as one square per scan", async () => {
    exportMock.mockResolvedValue({
      truncated: false,
      row_count: 3,
      entries: [scan(3, 3, "fail", ["legacy"]), scan(2, 2, "fail", ["legacy", "other"]), scan(1, 1, "pass")],
    })
    wrap()

    expect(await screen.findByText("Branch protection")).toBeInTheDocument()
    expect(screen.getByText(/failing since/)).toBeInTheDocument()
    expect(screen.getByRole("img", { name: /passing, failing, failing across the 3 scans from .* to .*, oldest first/ })).toBeInTheDocument()
  })

  it("says so when no stored scan shows the repo failing", async () => {
    exportMock.mockResolvedValue({ truncated: false, row_count: 1, entries: [scan(1, 1, "pass")] })
    wrap()

    expect(await screen.findByText(/No stored scan shows this repository failing/)).toBeInTheDocument()
  })

  it("says when the history is too long to read in full", async () => {
    exportMock.mockResolvedValue({ truncated: true, row_count: 0, entries: [] })
    wrap()

    expect(await screen.findByText(/too long to read in full/)).toBeInTheDocument()
    expect(screen.getByText(/None of the newest scans show/)).toBeInTheDocument()
  })

  it("warns that a truncated history may start later than the real start, while still showing the timelines", async () => {
    exportMock.mockResolvedValue({ truncated: true, row_count: 1, entries: [scan(1, 1, "fail", ["legacy"])] })
    wrap()

    expect(await screen.findByText("Branch protection")).toBeInTheDocument()
    expect(screen.getByText(/too long to read in full/)).toBeInTheDocument()
  })

  it("does not fetch while the Security tab is closed", () => {
    wrap(false)
    expect(exportMock).not.toHaveBeenCalled()
  })

  it("shows the error when the history cannot be loaded", async () => {
    exportMock.mockRejectedValue(new Error("history unavailable"))
    wrap()
    expect(await screen.findByText(/history unavailable/)).toBeInTheDocument()
  })
})
