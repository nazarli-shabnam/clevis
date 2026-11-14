import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const digestMock = vi.fn()
const verifyMock = vi.fn()
let workspaceAdmin = true

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { digest: (...a: unknown[]) => digestMock(...a) },
    auth: { adminVerifyEmail: (...a: unknown[]) => verifyMock(...a) },
  },
}))
vi.mock("@/lib/auth-context", () => ({
  useOptionalAuth: () => ({ user: { is_workspace_admin: workspaceAdmin } }),
}))

import { SmtpVerifyNotice } from "@/components/smtp-verify-notice"

function renderNotice() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <SmtpVerifyNotice orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  digestMock.mockReset()
  verifyMock.mockReset()
  workspaceAdmin = true
})
afterEach(cleanup)

describe("SmtpVerifyNotice", () => {
  it("renders nothing when SMTP is configured", async () => {
    digestMock.mockResolvedValue({ smtp_configured: true })
    renderNotice()
    await waitFor(() => expect(digestMock).toHaveBeenCalled())
    expect(screen.queryByRole("note")).not.toBeInTheDocument()
  })

  it("explains the problem and lets a workspace admin verify an address", async () => {
    digestMock.mockResolvedValue({ smtp_configured: false })
    verifyMock.mockResolvedValue({ ok: true, already_verified: false })
    renderNotice()
    expect(await screen.findByText("Email isn't set up on this server")).toBeInTheDocument()

    expect(screen.getByText(/doesn.t prove they own the address/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Email address to verify"), { target: { value: " bob@acme.com " } })
    fireEvent.click(screen.getByRole("button", { name: "Mark verified" }))

    await waitFor(() => expect(verifyMock).toHaveBeenCalledWith("bob@acme.com"))
    expect(await screen.findByRole("status")).toHaveTextContent("bob@acme.com is verified")
  })

  it("shows the server's error, e.g. an unknown account", async () => {
    digestMock.mockResolvedValue({ smtp_configured: false })
    verifyMock.mockRejectedValue(new Error("No account with that email"))
    renderNotice()
    fireEvent.change(await screen.findByLabelText("Email address to verify"), { target: { value: "x@acme.com" } })
    fireEvent.click(screen.getByRole("button", { name: "Mark verified" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't verify: No account with that email")
  })

  it("only explains, without a form, to an org admin who isn't a workspace admin", async () => {
    workspaceAdmin = false
    digestMock.mockResolvedValue({ smtp_configured: false })
    renderNotice()
    expect(await screen.findByText(/Ask a workspace admin to verify/)).toBeInTheDocument()
    expect(screen.queryByLabelText("Email address to verify")).not.toBeInTheDocument()
  })
})
