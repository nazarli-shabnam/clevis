import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const forgotMock = vi.fn()
const resetMock = vi.fn()
const changeMock = vi.fn()
const setSessionMock = vi.fn()
let searchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({ useSearchParams: () => searchParams }))
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ setSession: setSessionMock }) }))
vi.mock("@/lib/api/client", () => ({
  api: {
    auth: {
      forgotPassword: (...a: unknown[]) => forgotMock(...a),
      resetPassword: (...a: unknown[]) => resetMock(...a),
      changePassword: (...a: unknown[]) => changeMock(...a),
    },
  },
}))

import ForgotPasswordPage from "@/app/forgot-password/page"
import ResetPasswordPage from "@/app/reset-password/page"
import { ChangePasswordCard } from "@/components/change-password-card"
import { isPublicRoute } from "@/lib/public-routes"

function wrap(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  forgotMock.mockReset()
  resetMock.mockReset()
  changeMock.mockReset()
  setSessionMock.mockReset()
  searchParams = new URLSearchParams()
})
afterEach(cleanup)

describe("password pages are reachable signed out", () => {
  it("lists both routes as public", () => {
    expect(isPublicRoute("/forgot-password")).toBe(true)
    expect(isPublicRoute("/reset-password")).toBe(true)
  })
})

describe("ForgotPasswordPage", () => {
  it("asks for the email and gives the same answer whatever it was", async () => {
    forgotMock.mockResolvedValue({ ok: true })
    wrap(<ForgotPasswordPage />)
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: " a@b.com " } })
    fireEvent.click(screen.getByRole("button", { name: "Email me a reset link" }))

    await waitFor(() => expect(forgotMock).toHaveBeenCalledWith("a@b.com"))
    expect(await screen.findByRole("status")).toHaveTextContent("If an account with a password exists")
    expect(screen.getByRole("status")).toHaveTextContent("ask your administrator")
  })
})

describe("ResetPasswordPage", () => {
  it("reports a link without a token", () => {
    wrap(<ResetPasswordPage />)
    expect(screen.getByRole("alert")).toHaveTextContent("missing its token")
  })

  it("checks the password locally, then sets it with the token", async () => {
    searchParams = new URLSearchParams({ token: "tok123" })
    resetMock.mockResolvedValue({ ok: true })
    wrap(<ResetPasswordPage />)

    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "short" } })
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "short" } })
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }))
    expect(screen.getByRole("alert")).toHaveTextContent("at least 12 characters")
    expect(resetMock).not.toHaveBeenCalled()

    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "a-brand-new-password" } })
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "a-brand-new-password" } })
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }))

    await waitFor(() => expect(resetMock).toHaveBeenCalledWith("tok123", "a-brand-new-password"))
    expect(await screen.findByRole("status")).toHaveTextContent("Your password is changed")
  })

  it("shows an expired link's error and offers a new one", async () => {
    searchParams = new URLSearchParams({ token: "old" })
    resetMock.mockRejectedValue(new Error("This reset link is invalid or has expired"))
    wrap(<ResetPasswordPage />)
    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "a-brand-new-password" } })
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "a-brand-new-password" } })
    fireEvent.click(screen.getByRole("button", { name: "Set new password" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired")
    expect(screen.getByRole("link", { name: "Ask for a new link" })).toHaveAttribute("href", "/forgot-password")
  })
})

describe("ChangePasswordCard", () => {
  const fill = (current: string, next: string, confirm: string) => {
    fireEvent.change(screen.getByLabelText("Current password"), { target: { value: current } })
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: next } })
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: confirm } })
    fireEvent.click(screen.getByRole("button", { name: "Change password" }))
  }

  it("changes the password and keeps this session signed in with the fresh token", async () => {
    const user = { id: 1, email: "a@b.com", name: null, is_workspace_admin: false }
    changeMock.mockResolvedValue({ access_token: "new.jwt.tok", user, pending_invitations: [] })
    wrap(<ChangePasswordCard />)
    fill("old-password-123", "a-brand-new-password", "a-brand-new-password")

    await waitFor(() => expect(changeMock).toHaveBeenCalledWith("old-password-123", "a-brand-new-password"))
    await waitFor(() => expect(setSessionMock).toHaveBeenCalledWith("new.jwt.tok", user, []))
    expect(await screen.findByRole("status")).toHaveTextContent("Password changed")
    expect(screen.getByLabelText("Current password")).toHaveValue("")
  })

  it("validates locally and shows the server's refusal", async () => {
    changeMock.mockRejectedValue(new Error("Current password is incorrect"))
    wrap(<ChangePasswordCard />)

    fill("old-password-123", "a-brand-new-password", "different-password-1")
    expect(screen.getByRole("alert")).toHaveTextContent("do not match")
    expect(changeMock).not.toHaveBeenCalled()

    fill("wrong-password-1", "a-brand-new-password", "a-brand-new-password")
    expect(await screen.findByText("Current password is incorrect")).toBeInTheDocument()
    expect(setSessionMock).not.toHaveBeenCalled()
  })
})
