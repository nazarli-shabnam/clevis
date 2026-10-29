import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const createMock = vi.fn()
const revokeMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    waivers: {
      list: (...a: unknown[]) => listMock(...a),
      create: (...a: unknown[]) => createMock(...a),
      revoke: (...a: unknown[]) => revokeMock(...a),
    },
  },
}))

import { WaiversCard } from "@/components/waivers-card"
import { CheckCard } from "@/components/check-card"

const waiver = (over: Record<string, unknown> = {}) => ({
  id: 1,
  check_id: "repository_default_branch_protection_enabled",
  repos: null,
  reason: "legacy repo",
  owner: "platform",
  expires_at: new Date(Date.now() + 10 * 86_400_000).toISOString(),
  created_by: "a@e.com",
  created_at: new Date().toISOString(),
  active: true,
  ...over,
})

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <WaiversCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  listMock.mockReset()
  createMock.mockReset()
  revokeMock.mockReset()
})
afterEach(cleanup)

describe("WaiversCard", () => {
  it("lists waivers with scope, owner and expiry, flagging an expired one", async () => {
    listMock.mockResolvedValue([
      waiver(),
      waiver({ id: 2, repos: ["legacy-api"], active: false, expires_at: new Date(Date.now() - 86_400_000).toISOString() }),
    ])
    renderCard()
    expect(await screen.findByText("whole organization")).toBeInTheDocument()
    expect(screen.getByText("legacy-api")).toBeInTheDocument()
    expect(screen.getAllByText(/owner platform/)).toHaveLength(2)
    expect(screen.getByText(/^expired/)).toBeInTheDocument()
  })

  it("adds a waiver with a reason, an owner, optional repos and an end-of-day expiry", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue(waiver())
    renderCard()
    await screen.findByText("No waivers.")
    const add = screen.getByRole("button", { name: "Add waiver" })
    expect(add).toBeDisabled() // reason and owner are required

    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "legacy repo" } })
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "platform" } })
    fireEvent.change(screen.getByLabelText(/Repositories/), { target: { value: "legacy-api, old-site" } })
    fireEvent.click(add)

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    const [org, body] = createMock.mock.calls[0]
    expect(org).toBe("acme")
    expect(body).toMatchObject({
      check_id: "repository_default_branch_protection_enabled",
      repos: ["legacy-api", "old-site"],
      reason: "legacy repo",
      owner: "platform",
    })
    expect(new Date(body.expires_at).getTime()).toBeGreaterThan(Date.now())
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1)) // refreshed
  })

  it("revokes a waiver", async () => {
    listMock.mockResolvedValue([waiver()])
    revokeMock.mockResolvedValue(undefined)
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: /Revoke waiver for Default branch has protection rules/ }))
    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("acme", 1))
  })

  it("shows the server's error when adding fails", async () => {
    listMock.mockResolvedValue([])
    createMock.mockRejectedValue(new Error("A waiver can last at most 90 days"))
    renderCard()
    await screen.findByText("No waivers.")
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "r" } })
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "o" } })
    fireEvent.click(screen.getByRole("button", { name: "Add waiver" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("A waiver can last at most 90 days")
  })
})

describe("CheckCard waived badge", () => {
  it("shows when a failing check is waived, with the reason and owner on hover", () => {
    render(
      <CheckCard
        check={{
          id: "repository_default_branch_protection_enabled",
          title: "Default branch has protection rules",
          severity: "high",
          remediation: "Enable it.",
          status: "fail",
          scored: false,
          value: null,
          waived: { waiver_ids: [1], until: "2030-01-15T00:00:00Z", reason: "legacy repo", owner: "platform", repos: null },
        }}
      />,
    )
    const chip = screen.getByText(/^Waived until/)
    expect(chip).toHaveAttribute("title", expect.stringContaining("legacy repo (owner: platform"))
  })
})
