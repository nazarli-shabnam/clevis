import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const createMock = vi.fn()
const revokeMock = vi.fn()
const updateMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    waivers: {
      list: (...a: unknown[]) => listMock(...a),
      create: (...a: unknown[]) => createMock(...a),
      revoke: (...a: unknown[]) => revokeMock(...a),
      update: (...a: unknown[]) => updateMock(...a),
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
  updateMock.mockReset()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

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

  it("hides the repositories field and sends an org-wide waiver for a check that never names repos", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue(waiver())
    renderCard()
    await screen.findByText("No waivers.")
    expect(screen.getByLabelText(/Repositories/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText("Check"), { target: { value: "repository_license_present" } })
    expect(screen.queryByLabelText(/Repositories/)).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "legacy repo" } })
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "platform" } })
    fireEvent.click(screen.getByRole("button", { name: "Add waiver" }))

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    expect(createMock.mock.calls[0][1]).toMatchObject({ check_id: "repository_license_present", repos: [] })
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

  it("shows a load error with a retry that refetches", async () => {
    listMock.mockRejectedValueOnce(new Error("boom")).mockResolvedValue([])
    renderCard()
    expect(await screen.findByText(/Couldn't load waivers: boom/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("No waivers.")).toBeInTheDocument()
    expect(listMock).toHaveBeenCalledTimes(2)
  })

  it("labels a waiver for a check this UI doesn't know by its id", async () => {
    listMock.mockResolvedValue([waiver({ check_id: "some_future_check" })])
    renderCard()
    expect(await screen.findByText("some_future_check")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Revoke waiver for some_future_check" })).toBeInTheDocument()
  })

  it("shows why a revoke failed", async () => {
    listMock.mockResolvedValue([waiver()])
    revokeMock.mockRejectedValue(new Error("already gone"))
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: /Revoke waiver for/ }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }))
    expect(await screen.findByText(/Couldn't revoke: already gone/)).toBeInTheDocument()
  })

  it("sends the chosen check, shows the pending label, and bounds the expiry date", async () => {
    // Fix the clock at local noon; only Date is faked so waitFor/findBy keep working.
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date(2026, 5, 15, 12, 0, 0))
    listMock.mockResolvedValue([])
    let finish: (v: unknown) => void = () => {}
    createMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    renderCard()
    await screen.findByText("No waivers.")
    fireEvent.change(screen.getByLabelText("Check"), { target: { value: "repository_license_present" } })
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "r" } })
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "o" } })
    const until = screen.getByLabelText("Expires") as HTMLInputElement
    expect(until.min).toBe("2026-06-16") // the next day
    expect(until.value).toBe("2026-07-15") // 30 days out
    expect(until.max).toBe("2026-09-12") // day 89: the 90-day limit falls before the end of day 90
    fireEvent.click(screen.getByRole("button", { name: "Add waiver" }))
    expect(await screen.findByRole("button", { name: "Adding…" })).toBeDisabled()
    expect(createMock.mock.calls[0][1]).toMatchObject({ check_id: "repository_license_present" })
    finish(waiver())
  })

  it("revokes a waiver", async () => {
    listMock.mockResolvedValue([waiver()])
    revokeMock.mockResolvedValue(undefined)
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: /Revoke waiver for Default branch has protection rules/ }))
    expect(revokeMock).not.toHaveBeenCalled() // nothing happens until confirmed
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }))
    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("acme", 1))
  })

  it("leaves edit mode when the waiver being edited is revoked", async () => {
    listMock.mockResolvedValue([waiver()])
    revokeMock.mockResolvedValue(undefined)
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: /Renew or edit waiver for/ }))
    expect(await screen.findByRole("button", { name: "Save waiver" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Revoke waiver for/ }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }))
    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("acme", 1))
    expect(await screen.findByRole("button", { name: "Add waiver" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Save waiver" })).not.toBeInTheDocument()
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

  it("names the repos a repo-scoped waiver covered", () => {
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
          waived: { waiver_ids: [1], until: "2030-01-15T00:00:00Z", reason: "legacy", owner: "platform", repos: ["api", "web"] },
        }}
      />,
    )
    expect(screen.getByText(/^Waived until/)).toHaveAttribute("title", expect.stringContaining("repos: api, web"))
  })

  it("shows the days left on an active waiver", async () => {
    listMock.mockResolvedValue([waiver()]) // expires in 10 days
    renderCard()
    expect(await screen.findByText(/10 days left/)).toBeInTheDocument()
  })

  it("edits a waiver in place with PUT, prefilled from it, and returns to the add form", async () => {
    listMock.mockResolvedValue([waiver({ repos: ["legacy-api"] })])
    updateMock.mockResolvedValue(waiver())
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: /Renew or edit waiver for Default branch has protection rules/ }))

    expect(screen.getByRole("button", { name: "Save waiver" })).toBeInTheDocument()
    expect((screen.getByLabelText("Reason") as HTMLInputElement).value).toBe("legacy repo")
    expect((screen.getByLabelText("Owner") as HTMLInputElement).value).toBe("platform")
    expect((screen.getByLabelText(/Repositories/) as HTMLInputElement).value).toBe("legacy-api")

    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "still legacy" } })
    fireEvent.click(screen.getByRole("button", { name: "Save waiver" }))

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1))
    const [org, id, body] = updateMock.mock.calls[0]
    expect([org, id]).toEqual(["acme", 1])
    expect(body).toMatchObject({ reason: "still legacy", owner: "platform", repos: ["legacy-api"] })
    expect(createMock).not.toHaveBeenCalled()
    expect(await screen.findByRole("button", { name: "Add waiver" })).toBeInTheDocument()
  })

  it("renews an expired waiver, defaulting its new expiry to 30 days out, and can be cancelled", async () => {
    vi.useFakeTimers({ toFake: ["Date"] })
    vi.setSystemTime(new Date(2026, 5, 15, 12, 0, 0))
    listMock.mockResolvedValue([waiver({ active: false, expires_at: new Date(2026, 5, 1, 12, 0, 0).toISOString() })])
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: /Renew or edit waiver/ }))
    expect((screen.getByLabelText("Expires") as HTMLInputElement).value).toBe("2026-07-15")

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(screen.getByRole("button", { name: "Add waiver" })).toBeInTheDocument()
  })

  it("shows the server's error when saving an edit fails", async () => {
    listMock.mockResolvedValue([waiver()])
    updateMock.mockRejectedValue(new Error("Unknown check"))
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: /Renew or edit waiver/ }))
    fireEvent.click(screen.getByRole("button", { name: "Save waiver" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save the waiver: Unknown check")
  })

  it("lets an org admin waive a failing check from its card, prefilled with that check", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue(waiver())
    const check = {
      id: "repository_license_present",
      title: "License present",
      severity: "low",
      remediation: "Add a license",
      status: "fail" as const,
      value: null,
    }
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <CheckCard check={check} waiveOrg="acme" />
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByRole("button", { name: "Waive…" }))
    expect(screen.queryByLabelText("Check")).not.toBeInTheDocument() // the check is fixed
    expect(screen.queryByLabelText(/Repositories/)).not.toBeInTheDocument() // org-wide-only check
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "no license needed" } })
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "legal" } })
    fireEvent.click(screen.getByRole("button", { name: "Add waiver" }))

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1))
    expect(createMock.mock.calls[0][0]).toBe("acme")
    expect(createMock.mock.calls[0][1]).toMatchObject({ check_id: "repository_license_present", repos: [], reason: "no license needed" })
    expect(await screen.findByText(/Waiver added/)).toBeInTheDocument()
  })

  it.each(["organization_members_2fa_enabled", "repository_no_outside_collaborator_admins"])(
    "offers Waive for the identity check %s, which only takes an org-wide waiver",
    (id) => {
      const queryClient = new QueryClient()
      render(
        <QueryClientProvider client={queryClient}>
          <CheckCard
            check={{ id, title: "t", severity: "medium", remediation: "r", status: "fail", value: null }}
            waiveOrg="acme"
          />
        </QueryClientProvider>,
      )
      fireEvent.click(screen.getByRole("button", { name: "Waive…" }))
      expect(screen.queryByLabelText(/Repositories/)).not.toBeInTheDocument()
    },
  )

  it("offers no Waive action without an org to waive for, or on a passing check", () => {
    const base = { id: "repository_license_present", title: "License present", severity: "low", remediation: "x", value: null }
    const queryClient = new QueryClient()
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <CheckCard check={{ ...base, status: "fail" }} />
      </QueryClientProvider>,
    )
    expect(screen.queryByRole("button", { name: "Waive…" })).not.toBeInTheDocument()
    rerender(
      <QueryClientProvider client={queryClient}>
        <CheckCard check={{ ...base, status: "pass" }} waiveOrg="acme" />
      </QueryClientProvider>,
    )
    expect(screen.queryByRole("button", { name: "Waive…" })).not.toBeInTheDocument()
  })
})
