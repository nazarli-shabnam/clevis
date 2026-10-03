import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const myViewMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { analytics: { myView: (...a: unknown[]) => myViewMock(...a) } },
}))

import { DeveloperInbox, latestFailingRuns } from "@/components/developer-inbox"
import type { MyViewResponse, MyViewRunSummary } from "@/lib/api/types"

const NOW = new Date("2026-10-04T12:00:00Z").getTime()

function run(overrides: Partial<MyViewRunSummary>): MyViewRunSummary {
  return {
    repository: "acme/api",
    id: 1,
    name: "CI",
    status: "completed",
    conclusion: "failure",
    html_url: "https://github.com/acme/api/actions/runs/1",
    created_at: "2026-10-03T00:00:00Z",
    ...overrides,
  }
}

function view(overrides: Partial<MyViewResponse> = {}): MyViewResponse {
  return {
    my_open_prs: [],
    review_requests: [],
    assigned_issues: [],
    my_recent_runs: [],
    identity_unresolved: false,
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

describe("latestFailingRuns", () => {
  it("drops a failure that a later run of the same workflow fixed", () => {
    const runs = [
      run({ id: 1, created_at: "2026-10-01T00:00:00Z", conclusion: "failure" }),
      run({ id: 2, created_at: "2026-10-02T00:00:00Z", conclusion: "success" }),
    ]
    expect(latestFailingRuns(runs)).toEqual([])
  })

  it("keeps the latest failure per workflow and ignores in-progress reruns", () => {
    const runs = [
      run({ id: 1, created_at: "2026-10-01T00:00:00Z" }),
      run({ id: 2, created_at: "2026-10-02T00:00:00Z", conclusion: "timed_out" }),
      run({ id: 3, created_at: "2026-10-03T00:00:00Z", status: "in_progress", conclusion: null }),
      run({ id: 4, name: "Lint", conclusion: "cancelled" }),
    ]
    expect(latestFailingRuns(runs).map((r) => r.id)).toEqual([2])
  })
})

describe("DeveloperInbox", () => {
  it("lists waiting reviews oldest first with their age", async () => {
    myViewMock.mockResolvedValue(
      view({
        review_requests: [
          { number: 2, title: "Newer PR", repository: "acme/api", html_url: "u2", updated_at: "2026-10-03T00:00:00Z", created_at: "2026-10-02T00:00:00Z" },
          { number: 1, title: "Older PR", repository: "acme/api", html_url: "u1", updated_at: "2026-10-03T00:00:00Z", created_at: "2026-09-20T00:00:00Z" },
        ],
      }),
    )
    renderInbox()

    await waitFor(() => expect(screen.getByText("Older PR")).toBeInTheDocument())
    const titles = screen.getAllByText(/PR$/).map((el) => el.textContent)
    expect(titles).toEqual(["Older PR", "Newer PR"])
    expect(screen.getAllByText(/^opened /)).toHaveLength(2)
  })

  it("shows failing CI runs and empty states", async () => {
    myViewMock.mockResolvedValue(view({ my_recent_runs: [run({ name: "Build" })] }))
    renderInbox()

    await waitFor(() => expect(screen.getByText("Build")).toBeInTheDocument())
    expect(screen.getByText("No reviews waiting.")).toBeInTheDocument()
  })

  it("renders nothing when GitHub identity is unresolved", async () => {
    myViewMock.mockResolvedValue(view({ identity_unresolved: true }))
    const { container } = renderInbox()

    await waitFor(() => expect(myViewMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when the request fails", async () => {
    myViewMock.mockRejectedValue(new Error("boom"))
    const { container } = renderInbox()

    await waitFor(() => expect(myViewMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
