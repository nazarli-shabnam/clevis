import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const setRoleMock = vi.fn()
const removeMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    orgMemberships: {
      list: (...a: unknown[]) => listMock(...a),
      setRole: (...a: unknown[]) => setRoleMock(...a),
      remove: (...a: unknown[]) => removeMock(...a),
    },
  },
}))

import { ClevisMembersCard } from "@/components/clevis-members-card"

const member = (over: Record<string, unknown> = {}) => ({
  user_id: 1,
  email: "gh@example.com",
  name: null,
  github_login: "gh-user",
  role: "admin",
  source: "github",
  editable: false,
  ...over,
})
const contractor = (over: Record<string, unknown> = {}) =>
  member({ user_id: 2, email: "contractor@example.com", github_login: null, role: "member", source: "invite", editable: true, ...over })

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ClevisMembersCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  listMock.mockReset()
  setRoleMock.mockReset()
  removeMock.mockReset()
})
afterEach(cleanup)

describe("ClevisMembersCard", () => {
  it("shows GitHub-managed members read-only and invited ones with a role picker and Remove", async () => {
    listMock.mockResolvedValue([member(), contractor()])
    renderCard()

    expect(await screen.findByText("gh@example.com", { exact: false })).toBeInTheDocument()
    expect(screen.getByText("Managed by GitHub")).toBeInTheDocument()
    expect(screen.getByText("@gh-user")).toBeInTheDocument()
    expect(screen.queryByLabelText("Role of gh@example.com")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Remove gh@example.com" })).not.toBeInTheDocument()

    expect(screen.getByText("Invited")).toBeInTheDocument()
    expect(screen.getByLabelText("Role of contractor@example.com")).toHaveValue("member")
    expect(screen.getByRole("button", { name: "Remove contractor@example.com" })).toBeInTheDocument()
  })

  it("changes an invited member's role", async () => {
    listMock.mockResolvedValue([contractor()])
    setRoleMock.mockResolvedValue(contractor({ role: "admin" }))
    renderCard()

    fireEvent.change(await screen.findByLabelText("Role of contractor@example.com"), { target: { value: "admin" } })

    await waitFor(() => expect(setRoleMock).toHaveBeenCalledWith("acme", 2, "admin"))
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1)) // refreshed
  })

  it("shows why a role change was refused, e.g. the last admin", async () => {
    listMock.mockResolvedValue([contractor({ role: "admin" })])
    setRoleMock.mockRejectedValue(new Error("An organization needs at least one admin"))
    renderCard()

    fireEvent.change(await screen.findByLabelText("Role of contractor@example.com"), { target: { value: "member" } })

    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't change the role: An organization needs at least one admin")
  })

  it("removes a member only after confirming", async () => {
    listMock.mockResolvedValue([contractor()])
    removeMock.mockResolvedValue(undefined)
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: "Remove contractor@example.com" }))
    expect(removeMock).not.toHaveBeenCalled()

    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove" }))

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("acme", 2))
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument())
  })

  it("cancelling the confirmation removes nothing", async () => {
    listMock.mockResolvedValue([contractor()])
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: "Remove contractor@example.com" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument())
    expect(removeMock).not.toHaveBeenCalled()
  })

  it("shows why a removal failed", async () => {
    listMock.mockResolvedValue([contractor()])
    removeMock.mockRejectedValue(new Error("An organization needs at least one admin"))
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: "Remove contractor@example.com" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Remove" }))

    expect(await screen.findByText(/Couldn't remove the member: An organization needs at least one admin/)).toBeInTheDocument()
  })

  it("shows a load error with a retry", async () => {
    listMock.mockRejectedValueOnce(new Error("nope")).mockResolvedValue([member()])
    renderCard()

    expect(await screen.findByText(/Couldn't load members: nope/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    expect(await screen.findByText("Managed by GitHub")).toBeInTheDocument()
  })
})
