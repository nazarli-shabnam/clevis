import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const createMock = vi.fn()
const revokeMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    apiTokens: {
      list: (...a: unknown[]) => listMock(...a),
      create: (...a: unknown[]) => createMock(...a),
      revoke: (...a: unknown[]) => revokeMock(...a),
    },
  },
}))

import { ApiTokensCard } from "@/components/api-tokens-card"

const token = (over: Record<string, unknown> = {}) => ({
  id: 1,
  name: "ci",
  prefix: "clv_ab12",
  scope: "read",
  created_by: "a@e.com",
  created_at: new Date(Date.now() - 86_400_000).toISOString(),
  last_used_at: null,
  revoked_at: null,
  expires_at: null,
  ...over,
})

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ApiTokensCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  listMock.mockReset()
  createMock.mockReset()
  revokeMock.mockReset()
})
afterEach(cleanup)

describe("ApiTokensCard", () => {
  it("lists tokens with prefix, scope and last used, flagging revoked ones", async () => {
    listMock.mockResolvedValue([
      token(),
      token({ id: 2, name: "old", last_used_at: new Date().toISOString(), revoked_at: new Date().toISOString() }),
    ])
    renderCard()
    expect(await screen.findByText("ci")).toBeInTheDocument()
    expect(screen.getByText(/never used/)).toBeInTheDocument()
    expect(screen.getByText("revoked")).toBeInTheDocument()
    // Only the active token can be revoked.
    expect(screen.getAllByRole("button", { name: /^Revoke token/ })).toHaveLength(1)
  })

  it("creates a token and shows the secret once with a copy button", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ ...token({ name: "deploy" }), token: "clv_secret_value" })
    renderCard()
    await screen.findByText("No API tokens.")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " deploy " } })
    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "1" } })
    fireEvent.click(screen.getByRole("button", { name: "Create token" }))
    expect(await screen.findByText("clv_secret_value")).toBeInTheDocument()
    expect(createMock).toHaveBeenCalledWith("acme", "deploy", 30)
    expect(screen.getByRole("button", { name: "Copy token deploy" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "I've saved it" }))
    expect(screen.queryByText("clv_secret_value")).not.toBeInTheDocument()
  })

  it("revokes only after confirming, and a failure is shown on that row alone", async () => {
    listMock.mockResolvedValue([token(), token({ id: 2, name: "other" })])
    revokeMock.mockRejectedValue(new Error("boom"))
    renderCard()
    await screen.findByText("ci")
    fireEvent.click(screen.getByRole("button", { name: "Revoke token ci" }))
    expect(revokeMock).not.toHaveBeenCalled()
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }))
    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("acme", 1))
    expect(await screen.findByText(/Couldn't revoke: boom/)).toBeInTheDocument()
    expect(screen.getAllByRole("alert")).toHaveLength(1)
    expect(screen.getByRole("button", { name: "Revoke token other" })).toBeEnabled()
  })

  it("revokes a token after confirming and refreshes the list", async () => {
    listMock.mockResolvedValueOnce([token()]).mockResolvedValue([token({ revoked_at: new Date().toISOString() })])
    revokeMock.mockResolvedValue(undefined)
    renderCard()
    await screen.findByText("ci")
    fireEvent.click(screen.getByRole("button", { name: "Revoke token ci" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Revoke" }))
    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("acme", 1))
    await waitFor(() => expect(screen.queryByRole("button", { name: "Revoke token ci" })).not.toBeInTheDocument())
    expect(listMock).toHaveBeenCalledTimes(2)
  })

  it("shows when an active token expires", async () => {
    listMock.mockResolvedValue([token({ expires_at: new Date(Date.now() + 5 * 86_400_000).toISOString() })])
    renderCard()
    expect(await screen.findByText(/expires/)).toBeInTheDocument()
  })

  it("shows a load error and retries it", async () => {
    listMock.mockRejectedValueOnce(new Error("boom")).mockResolvedValue([token()])
    renderCard()
    expect(await screen.findByText(/Couldn't load API tokens: boom/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("ci")).toBeInTheDocument()
  })

  it("shows why a token could not be created", async () => {
    listMock.mockResolvedValue([])
    createMock.mockRejectedValue(new Error("Name already used"))
    renderCard()
    await screen.findByText("No API tokens.")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "ci" } })
    fireEvent.click(screen.getByRole("button", { name: "Create token" }))
    expect(await screen.findByText(/Couldn't create the token: Name already used/)).toBeInTheDocument()
  })

  it("won't create another token while the new secret is still showing", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ ...token({ name: "deploy" }), token: "clv_secret_value" })
    renderCard()
    await screen.findByText("No API tokens.")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "deploy" } })
    fireEvent.click(screen.getByRole("button", { name: "Create token" }))
    await screen.findByText("clv_secret_value")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "second" } })
    expect(screen.getByRole("button", { name: "Create token" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "I've saved it" }))
    expect(screen.getByRole("button", { name: "Create token" })).toBeEnabled()
  })
})
