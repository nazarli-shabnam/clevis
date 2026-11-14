import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const createMock = vi.fn()
const revokeMock = vi.fn()
let searchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({ useSearchParams: () => searchParams }))

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
  searchParams = new URLSearchParams()
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

  it("explains an empty list and the button takes you to the create form (#993)", async () => {
    listMock.mockResolvedValue([])
    renderCard()
    await screen.findByText(/No API tokens yet/)
    fireEvent.click(screen.getByRole("button", { name: "Create a token" }))
    expect(screen.getByLabelText("Name")).toHaveFocus()
  })

  it("shows a copyable workflow snippet with the org filled in and never the secret (#993)", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ ...token({ name: "deploy" }), token: "clv_secret_value" })
    renderCard()
    await screen.findByText(/No API tokens yet/)
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "deploy" } })
    fireEvent.click(screen.getByRole("button", { name: "Create token" }))
    await screen.findByText("clv_secret_value")

    const snippet = screen.getByText(/uses: nazarli-shabnam\/clevis\/actions\/clevis-score@/).textContent!
    expect(snippet).toContain("uses: nazarli-shabnam/clevis/actions/clevis-score@")
    expect(snippet).toContain("org: acme")
    expect(snippet).toContain("token: ${{ secrets.CLEVIS_API_TOKEN }}")
    expect(snippet).not.toContain("clv_secret_value")
    expect(screen.getByRole("button", { name: "Copy workflow snippet" })).toBeInTheDocument()
  })

  it("creates a token and shows the secret once with a copy button", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ ...token({ name: "deploy" }), token: "clv_secret_value" })
    renderCard()
    await screen.findByText(/No API tokens yet/)
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
    await screen.findByText(/No API tokens yet/)
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "ci" } })
    fireEvent.click(screen.getByRole("button", { name: "Create token" }))
    expect(await screen.findByText(/Couldn't create the token: Name already used/)).toBeInTheDocument()
  })

  it("won't create another token while the new secret is still showing", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue({ ...token({ name: "deploy" }), token: "clv_secret_value" })
    renderCard()
    await screen.findByText(/No API tokens yet/)
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "deploy" } })
    fireEvent.click(screen.getByRole("button", { name: "Create token" }))
    await screen.findByText("clv_secret_value")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "second" } })
    expect(screen.getByRole("button", { name: "Create token" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "I've saved it" }))
    expect(screen.getByRole("button", { name: "Create token" })).toBeEnabled()
  })

  describe("expiry warning and rotation (#954)", () => {
    const DAY = 86_400_000
    const expiring = (over: Record<string, unknown> = {}) =>
      token({
        name: "ci-gate",
        created_at: new Date(Date.now() - 76 * DAY).toISOString(),
        expires_at: new Date(Date.now() + 14 * DAY - 3_600_000).toISOString(),
        ...over,
      })

    it("flags a token expiring within 14 days, and not one further out, never-expiring or revoked", async () => {
      listMock.mockResolvedValue([
        expiring(),
        token({ id: 2, name: "far", expires_at: new Date(Date.now() + 40 * DAY).toISOString() }),
        token({ id: 3, name: "never" }),
        expiring({ id: 4, name: "gone", revoked_at: new Date().toISOString() }),
      ])
      renderCard()
      expect(await screen.findByText("expires in 14 days")).toBeInTheDocument()
      expect(screen.getAllByText(/^expires in \d+ days?$/)).toHaveLength(1)
    })

    it("rotates into a same-name, same-lifetime token, shows the secret once and keeps the old one working", async () => {
      listMock.mockResolvedValue([expiring()])
      createMock.mockResolvedValue({ ...token({ id: 9, name: "ci-gate", prefix: "clv_new1" }), token: "clv_newsecret" })
      renderCard()
      fireEvent.click(await screen.findByRole("button", { name: "Rotate token ci-gate" }))
      expect(await screen.findByText("clv_newsecret")).toBeInTheDocument()
      // 90-day original lifetime (created 76 days ago, expires in ~14).
      expect(createMock).toHaveBeenCalledWith("acme", "ci-gate", 90)
      expect(revokeMock).not.toHaveBeenCalled()
      // A second rotation would overwrite the secret still on screen.
      expect(screen.getByRole("button", { name: "Rotate token ci-gate" })).toBeDisabled()
    })

    it("rotates a never-expiring token into another one that never expires", async () => {
      listMock.mockResolvedValue([token({ name: "forever" })])
      createMock.mockResolvedValue({ ...token({ id: 9, name: "forever" }), token: "clv_x" })
      renderCard()
      fireEvent.click(await screen.findByRole("button", { name: "Rotate token forever" }))
      await waitFor(() => expect(createMock).toHaveBeenCalledWith("acme", "forever", undefined))
    })

    it("revokes the old token only after confirmation", async () => {
      listMock.mockResolvedValue([expiring({ id: 5 })])
      createMock.mockResolvedValue({ ...token({ id: 9, name: "ci-gate" }), token: "clv_newsecret" })
      revokeMock.mockResolvedValue(undefined)
      renderCard()
      fireEvent.click(await screen.findByRole("button", { name: "Rotate token ci-gate" }))
      fireEvent.click(await screen.findByRole("button", { name: "Revoke old token" }))
      expect(revokeMock).not.toHaveBeenCalled()
      const dialog = await screen.findByRole("alertdialog")
      fireEvent.click(within(dialog).getByRole("button", { name: "Revoke" }))
      await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("acme", 5))
      await waitFor(() => expect(screen.queryByRole("button", { name: "Revoke old token" })).not.toBeInTheDocument())
      // The new secret is still on screen until it is acknowledged.
      expect(screen.getByText("clv_newsecret")).toBeInTheDocument()
    })

    it("reports a failed rotation and creates nothing to revoke", async () => {
      listMock.mockResolvedValue([expiring()])
      createMock.mockRejectedValue(new Error("At most 20 active tokens per organization"))
      renderCard()
      fireEvent.click(await screen.findByRole("button", { name: "Rotate token ci-gate" }))
      expect(await screen.findByRole("alert")).toHaveTextContent("At most 20 active tokens per organization")
      expect(screen.queryByRole("button", { name: "Revoke old token" })).not.toBeInTheDocument()
    })

    it("marks the token a notification links to with ?token=", async () => {
      searchParams = new URLSearchParams("token=2")
      listMock.mockResolvedValue([token(), token({ id: 2, name: "linked" })])
      renderCard()
      const row = (await screen.findByText("linked")).closest("li")
      expect(row).toHaveAttribute("aria-current", "true")
      expect(screen.getByText("ci").closest("li")).not.toHaveAttribute("aria-current")
    })
  })
})
