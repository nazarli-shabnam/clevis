import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const myViewMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { analytics: { myView: (...a: unknown[]) => myViewMock(...a) } },
}))

import { DeveloperInbox, sortByCiStatus } from "@/components/developer-inbox"
import type { MyViewPRSummary, MyViewResponse } from "@/lib/api/types"

const NOW = new Date("2026-10-04T12:00:00Z").getTime()

function pr(overrides: Partial<MyViewPRSummary> = {}): MyViewPRSummary {
  return {
    number: 1,
    title: "A PR",
    repository: "acme/api",
    html_url: "https://github.com/acme/api/pull/1",
    updated_at: "2026-10-03T00:00:00Z",
    created_at: "2026-10-02T00:00:00Z",
    ...overrides,
  }
}

function view(overrides: Partial<MyViewResponse> = {}): MyViewResponse {
  return {
    my_open_prs: [],
    review_requests: [],
    assigned_issues: [],
    identity_unresolved: false,
    my_open_prs_total: 0,
    review_requests_total: 0,
    assigned_issues_total: 0,
    incomplete: false,
    ...overrides,
  }
}

function renderInbox() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <DeveloperInbox org="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] })
  vi.setSystemTime(NOW)
  myViewMock.mockReset()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("sortByCiStatus", () => {
  it("puts failing PRs first, then pending, unknown and passing, newest activity first within a state", () => {
    const sorted = sortByCiStatus([
      pr({ number: 1, ci_status: "passing" }),
      pr({ number: 2, ci_status: "failing", updated_at: "2026-10-01T00:00:00Z" }),
      pr({ number: 3, ci_status: "pending" }),
      pr({ number: 4, ci_status: "failing", updated_at: "2026-10-03T00:00:00Z" }),
      pr({ number: 5, ci_status: null }),
    ])
    expect(sorted.map((p) => p.number)).toEqual([4, 2, 3, 5, 1])
  })
})

describe("DeveloperInbox", () => {
  it("lists waiting reviews oldest first, labelled as when the PR was opened", async () => {
    myViewMock.mockResolvedValue(
      view({
        review_requests_total: 2,
        review_requests: [
          pr({ number: 2, title: "Newer PR", created_at: "2026-10-02T00:00:00Z" }),
          pr({ number: 1, title: "Older PR", created_at: "2026-09-20T00:00:00Z" }),
        ],
      }),
    )
    renderInbox()

    await waitFor(() => expect(screen.getByText("Older PR")).toBeInTheDocument())
    expect(screen.getAllByText(/PR$/).map((el) => el.textContent)).toEqual(["Older PR", "Newer PR"])
    expect(screen.getAllByText(/^PR opened /)).toHaveLength(2)
  })

  it("shows GitHub's real total, not the number of listed rows, with a view-all link", async () => {
    myViewMock.mockResolvedValue(
      view({ review_requests_total: 37, review_requests: [pr({ number: 1, title: "Only listed row" })] }),
    )
    renderInbox()

    await waitFor(() => expect(screen.getByText("Only listed row")).toBeInTheDocument())
    expect(screen.getByText("37")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /View all 37/ })).toHaveAttribute("href", "/my?tab=reviews")
  })

  it("shows each open PR's CI state, failing first, and the assigned issues", async () => {
    myViewMock.mockResolvedValue(
      view({
        my_open_prs_total: 2,
        my_open_prs: [pr({ number: 1, title: "Green PR", ci_status: "passing" }), pr({ number: 2, title: "Red PR", ci_status: "failing" })],
        assigned_issues_total: 1,
        assigned_issues: [{ number: 9, title: "Flaky test", repository: "acme/api", html_url: "u", updated_at: "2026-10-03T00:00:00Z" }],
      }),
    )
    renderInbox()

    await waitFor(() => expect(screen.getByText("Red PR")).toBeInTheDocument())
    expect(screen.getAllByText(/^(Red|Green) PR$/).map((el) => el.textContent)).toEqual(["Red PR", "Green PR"])
    expect(screen.getByText("CI failing")).toBeInTheDocument()
    expect(screen.getByText("CI passing")).toBeInTheDocument()
    expect(screen.getByText("Flaky test")).toBeInTheDocument()
    expect(screen.getByText("No reviews waiting.")).toBeInTheDocument()
  })

  it("warns that counts may be incomplete when a GitHub search failed", async () => {
    myViewMock.mockResolvedValue(view({ incomplete: true }))
    renderInbox()

    expect(await screen.findByRole("alert")).toHaveTextContent(/may be incomplete/)
    // An empty section must not claim there is nothing waiting when its search failed.
    expect(screen.queryByText("No reviews waiting.")).not.toBeInTheDocument()
    expect(screen.getAllByText("Couldn't load.")).toHaveLength(3)
  })

  it("renders nothing when GitHub identity is unresolved", async () => {
    myViewMock.mockResolvedValue(view({ identity_unresolved: true }))
    const { container } = renderInbox()

    await waitFor(() => expect(myViewMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("shows the error with a retry instead of vanishing when the request fails", async () => {
    myViewMock.mockRejectedValue(new Error("boom"))
    renderInbox()

    expect(await screen.findByText("boom")).toBeInTheDocument()
    myViewMock.mockResolvedValue(view({ review_requests_total: 1, review_requests: [pr({ title: "Recovered" })] }))
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))

    expect(await screen.findByText("Recovered")).toBeInTheDocument()
  })
})
