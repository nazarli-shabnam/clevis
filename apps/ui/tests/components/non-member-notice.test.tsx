import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const minesMock = vi.fn()
let scope: { kind: "org" | "personal"; login: string } | null = { kind: "org", login: "legacy" }

vi.mock("@/lib/active-scope", () => ({ useActiveScope: () => ({ scope }) }))
vi.mock("@/lib/api/client", () => ({ api: { orgs: { mine: (...a: unknown[]) => minesMock(...a) } } }))

import { NonMemberNotice } from "@/components/non-member-notice"

function renderNotice() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <NonMemberNotice />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  minesMock.mockReset()
  scope = { kind: "org", login: "legacy" }
})
afterEach(cleanup)

describe("NonMemberNotice (#1038)", () => {
  it("explains an org scope the caller holds only through a saved token", async () => {
    minesMock.mockResolvedValue([{ org_login: "acme", role: "admin" }])
    renderNotice()

    expect(await screen.findByRole("status")).toHaveTextContent("You are not a member of legacy in Clevis")
    expect(screen.getByRole("link", { name: "remove the saved token" })).toHaveAttribute("href", "/settings")
  })

  it("says nothing for an org the caller belongs to (any casing)", async () => {
    minesMock.mockResolvedValue([{ org_login: "Legacy", role: "member" }])
    renderNotice()

    await waitFor(() => expect(minesMock).toHaveBeenCalled())
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("says nothing for a personal scope or while memberships are still loading", async () => {
    scope = { kind: "personal", login: "alice" }
    renderNotice()
    expect(minesMock).not.toHaveBeenCalled()
    expect(screen.queryByRole("status")).toBeNull()
  })
})
