import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const createMock = vi.fn()
const approveMock = vi.fn()
const denyMock = vi.fn()
const checksMock = vi.fn()
const waiversCreateMock = vi.fn()
const catalog = [
  { id: "repository_default_branch_protection_enabled", title: "Default branch has protection rules", org_wide_only: false },
]

let searchParams = new URLSearchParams()
vi.mock("next/navigation", () => ({ useSearchParams: () => searchParams }))

vi.mock("@/lib/api/client", () => ({
  api: {
    waivers: { checks: (...a: unknown[]) => checksMock(...a), create: (...a: unknown[]) => waiversCreateMock(...a) },
    waiverRequests: {
      list: (...a: unknown[]) => listMock(...a),
      create: (...a: unknown[]) => createMock(...a),
      approve: (...a: unknown[]) => approveMock(...a),
      deny: (...a: unknown[]) => denyMock(...a),
    },
  },
}))

import { WaiverRequestsCard } from "@/components/waiver-requests-card"
import { WaiverForm } from "@/components/waiver-form"

const request = (over: Record<string, unknown> = {}) => ({
  id: 1,
  check_id: "repository_default_branch_protection_enabled",
  repos: ["legacy"],
  reason: "migrating off",
  requested_expires_at: new Date(Date.now() + 10 * 86_400_000).toISOString(),
  requested_by: "dev@e.com",
  status: "pending",
  decided_by: null,
  decided_at: null,
  decision_note: null,
  waiver_id: null,
  created_at: new Date().toISOString(),
  ...over,
})

function wrap(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

beforeEach(() => {
  searchParams = new URLSearchParams()
  for (const m of [listMock, createMock, approveMock, denyMock, checksMock, waiversCreateMock]) m.mockReset()
  checksMock.mockResolvedValue(catalog)
})
afterEach(cleanup)

describe("WaiverRequestsCard", () => {
  it("renders nothing while no request is waiting", async () => {
    listMock.mockResolvedValue([])
    const { container } = wrap(<WaiverRequestsCard orgLogin="acme" />)
    await waitFor(() => expect(listMock).toHaveBeenCalledWith("acme", "pending"))
    expect(container).toBeEmptyDOMElement()
  })

  it("lists pending requests and approves one", async () => {
    listMock.mockResolvedValue([request()])
    approveMock.mockResolvedValue(request({ status: "approved" }))
    wrap(<WaiverRequestsCard orgLogin="acme" />)

    await screen.findByText(/dev@e.com asked to waive/)
    expect(screen.getByText("1 waiting")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Approve the request to waive/ }))

    await waitFor(() => expect(approveMock).toHaveBeenCalledWith("acme", 1))
  })

  it("denies a request", async () => {
    listMock.mockResolvedValue([request()])
    denyMock.mockResolvedValue(request({ status: "denied" }))
    wrap(<WaiverRequestsCard orgLogin="acme" />)

    fireEvent.click(await screen.findByRole("button", { name: /Deny the request to waive/ }))

    await waitFor(() => expect(denyMock).toHaveBeenCalledWith("acme", 1))
  })

  it("marks the request a notification linked to", async () => {
    searchParams = new URLSearchParams({ waiver_request: "2" })
    listMock.mockResolvedValue([request({ id: 1 }), request({ id: 2, requested_by: "other@e.com" })])
    wrap(<WaiverRequestsCard orgLogin="acme" />)

    await screen.findByText(/other@e.com asked to waive/)
    expect(document.getElementById("waiver-request-2")).toHaveAttribute("aria-current", "true")
    expect(document.getElementById("waiver-request-1")).not.toHaveAttribute("aria-current")
  })

  it("shows the server's error when a decision fails", async () => {
    listMock.mockResolvedValue([request()])
    approveMock.mockRejectedValue(new Error("This request was already denied"))
    wrap(<WaiverRequestsCard orgLogin="acme" />)

    fireEvent.click(await screen.findByRole("button", { name: /Approve the request to waive/ }))

    await screen.findByText(/already denied/)
  })
})

describe("WaiverForm as a request", () => {
  it("has no owner field and sends a request instead of a waiver", async () => {
    createMock.mockResolvedValue(request())
    const onSaved = vi.fn()
    wrap(<WaiverForm orgLogin="acme" fixedCheckId="repository_default_branch_protection_enabled" compact asRequest onSaved={onSaved} />)

    expect(screen.queryByLabelText("Owner")).not.toBeInTheDocument()
    fireEvent.change(await screen.findByLabelText("Reason"), { target: { value: "migrating off" } })
    fireEvent.change(screen.getByLabelText(/Repositories/), { target: { value: "legacy" } })
    fireEvent.click(screen.getByRole("button", { name: "Send request" }))

    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(waiversCreateMock).not.toHaveBeenCalled()
    expect(createMock).toHaveBeenCalledWith(
      "acme",
      expect.objectContaining({ check_id: "repository_default_branch_protection_enabled", repos: ["legacy"], reason: "migrating off" }),
    )
    expect(createMock.mock.calls[0][1]).not.toHaveProperty("owner")
  })

  it("shows why a request could not be sent", async () => {
    createMock.mockRejectedValue(new Error("You already have 5 requests waiting for an admin"))
    wrap(<WaiverForm orgLogin="acme" fixedCheckId="repository_default_branch_protection_enabled" compact asRequest />)

    fireEvent.change(await screen.findByLabelText("Reason"), { target: { value: "x" } })
    fireEvent.click(screen.getByRole("button", { name: "Send request" }))

    await screen.findByText(/send the request: You already have 5 requests/)
  })
})
