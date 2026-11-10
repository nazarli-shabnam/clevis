import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const meMock = vi.fn()
const resendMock = vi.fn()
let mockUser: { id: number } | null = { id: 1 }

vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: mockUser }) }))
vi.mock("@/lib/api/client", () => ({
  api: { auth: { me: (...a: unknown[]) => meMock(...a), resendVerification: (...a: unknown[]) => resendMock(...a) } },
}))

import { EmailVerificationBanner } from "@/components/email-verification-banner"

const me = (email_verified: boolean) => ({
  id: 1,
  email: "new@example.com",
  name: null,
  is_workspace_admin: false,
  email_verified,
})

function renderBanner() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <EmailVerificationBanner />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  meMock.mockReset()
  resendMock.mockReset()
  mockUser = { id: 1 }
})
afterEach(cleanup)

describe("EmailVerificationBanner", () => {
  it("tells an unverified user why it matters and which address got the link", async () => {
    meMock.mockResolvedValue(me(false))
    renderBanner()

    expect(await screen.findByText(/Verify your email to accept invitations/)).toBeInTheDocument()
    expect(screen.getByText("new@example.com")).toBeInTheDocument()
  })

  it("renders nothing for a verified user, while loading, when the lookup fails, or when signed out", async () => {
    meMock.mockResolvedValue(me(true))
    const { container } = renderBanner()
    await waitFor(() => expect(meMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    cleanup()

    meMock.mockRejectedValue(new Error("boom"))
    const failed = renderBanner()
    await waitFor(() => expect(meMock).toHaveBeenCalledTimes(2))
    expect(failed.container).toBeEmptyDOMElement()
    cleanup()

    mockUser = null
    meMock.mockClear()
    const signedOut = renderBanner()
    expect(signedOut.container).toBeEmptyDOMElement()
    expect(meMock).not.toHaveBeenCalled()
  })

  it("resends the email and confirms it", async () => {
    meMock.mockResolvedValue(me(false))
    resendMock.mockResolvedValue({ ok: true })
    renderBanner()

    fireEvent.click(await screen.findByRole("button", { name: "Resend email" }))

    expect(await screen.findByText(/Verification email sent/)).toBeInTheDocument()
    expect(resendMock).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole("button", { name: "Resend email" })).not.toBeInTheDocument()
  })

  it("shows the API's message when resending fails, e.g. a rate limit, and allows another try", async () => {
    meMock.mockResolvedValue(me(false))
    resendMock.mockRejectedValue(new Error("Too many requests. Try again later."))
    renderBanner()

    fireEvent.click(await screen.findByRole("button", { name: "Resend email" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests. Try again later.")
    expect(screen.getByRole("button", { name: "Resend email" })).toBeEnabled()
  })

  it("re-reads the account when the server says it is already verified, which removes the banner", async () => {
    meMock.mockResolvedValueOnce(me(false)).mockResolvedValue(me(true))
    resendMock.mockResolvedValue({ ok: true, already_verified: true })
    renderBanner()

    fireEvent.click(await screen.findByRole("button", { name: "Resend email" }))

    await waitFor(() => expect(screen.queryByText(/Verify your email/)).not.toBeInTheDocument())
  })

  it("can be dismissed", async () => {
    meMock.mockResolvedValue(me(false))
    renderBanner()
    await screen.findByText(/Verify your email/)

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }))

    expect(screen.queryByText(/Verify your email/)).not.toBeInTheDocument()
  })
})
