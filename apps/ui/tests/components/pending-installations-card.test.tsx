import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const pendingMock = vi.fn()
const syncMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    installations: {
      pending: (...a: unknown[]) => pendingMock(...a),
      sync: (...a: unknown[]) => syncMock(...a),
    },
  },
}))

import { PendingInstallationsCard } from "@/components/pending-installations-card"

const pending = (over: Record<string, unknown> = {}) => ({
  installation_id: 501,
  account_login: "acme",
  created_at: new Date(Date.now() - 3_600_000).toISOString(),
  role: "approver",
  ...over,
})

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const spy = vi.spyOn(queryClient, "invalidateQueries")
  const view = render(
    <QueryClientProvider client={queryClient}>
      <PendingInstallationsCard />
    </QueryClientProvider>,
  )
  return { ...view, spy }
}

describe("PendingInstallationsCard (#1014)", () => {
  beforeEach(() => {
    pendingMock.mockReset()
    syncMock.mockReset()
  })
  afterEach(cleanup)

  it("renders nothing when nothing is pending, or the list can't be loaded", async () => {
    pendingMock.mockResolvedValue([])
    const { container } = renderCard()
    await waitFor(() => expect(pendingMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    cleanup()

    pendingMock.mockRejectedValue(new Error("down"))
    const failed = renderCard()
    await waitFor(() => expect(pendingMock).toHaveBeenCalledTimes(2))
    expect(failed.container).toBeEmptyDOMElement()
  })

  it("lets the approving owner connect it with the same checked sync call, then refreshes", async () => {
    pendingMock.mockResolvedValue([pending()])
    syncMock.mockResolvedValue({ synced: true, token_ref: "tok_acme" })
    const { spy } = renderCard()

    expect(await screen.findByText("acme")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Connect to Clevis" }))

    await waitFor(() =>
      expect(syncMock).toHaveBeenCalledWith(
        { scope: "org", orgLogin: "acme" },
        { account_login: "acme", account_type: "Organization", installation_id: 501 },
      ),
    )
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["pending-installations"] }))
    expect(spy).toHaveBeenCalledWith({ queryKey: ["my-orgs"] })
  })

  it("shows progress while connecting and disables the button", async () => {
    pendingMock.mockResolvedValue([pending()])
    syncMock.mockReturnValue(new Promise(() => {}))
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: "Connect to Clevis" }))
    expect(await screen.findByRole("button", { name: /Connecting/ })).toBeDisabled()
  })

  it("shows the server's reason when connecting is refused and keeps the notice", async () => {
    pendingMock.mockResolvedValue([pending()])
    syncMock.mockRejectedValue(new Error("Org not found"))
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: "Connect to Clevis" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't connect it: Org not found")
    expect(screen.getByRole("button", { name: "Connect to Clevis" })).toBeEnabled()
  })

  it("tells the requester what is still needed and offers no connect button", async () => {
    pendingMock.mockResolvedValue([pending({ role: "requester" })])
    renderCard()
    expect(await screen.findByText(/An organization owner still has to connect it/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Connect to Clevis" })).not.toBeInTheDocument()
  })
})
