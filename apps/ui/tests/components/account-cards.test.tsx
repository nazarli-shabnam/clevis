import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const requestMock = vi.fn()
const confirmMock = vi.fn()
const deleteMock = vi.fn()
const logoutMock = vi.fn()
let searchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({ useSearchParams: () => searchParams }))
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { id: 1, email: "me@example.com" }, logout: logoutMock }),
}))
vi.mock("@/lib/api/client", () => ({
  api: {
    auth: {
      requestEmailChange: (...a: unknown[]) => requestMock(...a),
      confirmEmailChange: (...a: unknown[]) => confirmMock(...a),
      deleteAccount: (...a: unknown[]) => deleteMock(...a),
    },
  },
}))

import ConfirmEmailChangePage from "@/app/confirm-email-change/page"
import { ChangeEmailCard, DeleteAccountCard } from "@/components/account-cards"
import { isPublicRoute } from "@/lib/public-routes"

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  requestMock.mockReset()
  confirmMock.mockReset()
  deleteMock.mockReset()
  logoutMock.mockReset()
  searchParams = new URLSearchParams()
})
afterEach(cleanup)

describe("account cards", () => {
  it("asks for a confirmation link to the new address", async () => {
    requestMock.mockResolvedValue({ ok: true })
    wrap(<ChangeEmailCard />)
    fireEvent.change(screen.getByLabelText("New email"), { target: { value: " new@example.com " } })
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw-123456789012" } })
    fireEvent.click(screen.getByRole("button", { name: "Send confirmation link" }))

    await waitFor(() => expect(requestMock).toHaveBeenCalledWith("new@example.com", "pw-123456789012"))
    expect(await screen.findByRole("status")).toHaveTextContent("Check new@example.com")
  })

  it("shows why an email change was refused", async () => {
    requestMock.mockRejectedValue(new Error("Email isn't configured on this server"))
    wrap(<ChangeEmailCard />)
    fireEvent.change(screen.getByLabelText("New email"), { target: { value: "new@example.com" } })
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } })
    fireEvent.click(screen.getByRole("button", { name: "Send confirmation link" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("isn't configured")
  })

  it("only enables delete once the email is typed, asks again, then signs out", async () => {
    deleteMock.mockResolvedValue(undefined)
    wrap(<DeleteAccountCard />)
    const button = screen.getByRole("button", { name: "Delete my account" })
    expect(button).toBeDisabled()

    fireEvent.change(screen.getByLabelText("Type your email to confirm"), { target: { value: "ME@example.com" } })
    fireEvent.change(screen.getByLabelText("Password (if you have one)"), { target: { value: "pw-123456789012" } })
    fireEvent.click(button)
    expect(deleteMock).not.toHaveBeenCalled()
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }))

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("ME@example.com", "pw-123456789012"))
    await waitFor(() => expect(logoutMock).toHaveBeenCalled())
  })

  it("shows a refusal such as being the only admin and stays signed in", async () => {
    deleteMock.mockRejectedValue(new Error("You are the only admin of acme"))
    wrap(<DeleteAccountCard />)
    fireEvent.change(screen.getByLabelText("Type your email to confirm"), { target: { value: "me@example.com" } })
    fireEvent.click(screen.getByRole("button", { name: "Delete my account" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("only admin of acme")
    expect(logoutMock).not.toHaveBeenCalled()
  })
})

describe("ConfirmEmailChangePage", () => {
  it("is public and confirms once with the token", async () => {
    expect(isPublicRoute("/confirm-email-change")).toBe(true)
    searchParams = new URLSearchParams({ token: "tok" })
    confirmMock.mockResolvedValue({ ok: true })
    wrap(<ConfirmEmailChangePage />)
    expect(await screen.findByText(/Your email is changed/)).toBeInTheDocument()
    expect(confirmMock).toHaveBeenCalledTimes(1)
    expect(confirmMock).toHaveBeenCalledWith("tok")
  })

  it("explains an invalid link", async () => {
    searchParams = new URLSearchParams({ token: "bad" })
    confirmMock.mockRejectedValue(new Error("This confirmation link is invalid or has expired"))
    wrap(<ConfirmEmailChangePage />)
    expect(await screen.findByText(/invalid or has expired/)).toBeInTheDocument()
  })
})
