import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const statusMock = vi.fn()
const manifestMock = vi.fn()
const convertMock = vi.fn()
const replaceMock = vi.fn()
let search = new URLSearchParams()

vi.mock("@/lib/api/client", () => ({
  api: {
    githubApp: {
      status: (...a: unknown[]) => statusMock(...a),
      manifest: (...a: unknown[]) => manifestMock(...a),
      convert: (...a: unknown[]) => convertMock(...a),
    },
  },
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => search,
}))

import { GithubAppSetupCard } from "@/components/github-app-setup-card"
import GithubAppCallbackPage from "@/app/settings/github-app-callback/page"

const status = (over: Record<string, unknown> = {}) => ({
  source: "none",
  configured: false,
  app_id: null,
  slug: null,
  permissions: { metadata: "read", administration: "write" },
  read_only_permissions: { metadata: "read" },
  events: ["push", "member"],
  ...over,
})
const manifest = { post_url: "https://github.com/settings/apps/new?state=S", manifest: { name: "Clevis" }, state: "S" }

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <GithubAppSetupCard />
    </QueryClientProvider>,
  )
}

let submitted: { action: string; manifest: string } | null = null

beforeEach(() => {
  ;[statusMock, manifestMock, convertMock, replaceMock].forEach((m) => m.mockReset())
  search = new URLSearchParams()
  submitted = null
  vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(function (this: HTMLFormElement) {
    submitted = { action: this.action, manifest: (this.elements.namedItem("manifest") as HTMLInputElement).value }
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("GithubAppSetupCard: permissions the App itself lacks", () => {
  it("lists them with a link to the org-owned App's permissions page", async () => {
    statusMock.mockResolvedValue(
      status({
        source: "database",
        configured: true,
        slug: "clevis-acme",
        app_missing_permissions: { issues: "write", workflows: "write" },
        app_owner_login: "acme",
        app_owner_type: "Organization",
      }),
    )
    renderCard()
    expect(await screen.findByText(/doesn.t request 2 permissions Clevis uses/i)).toBeInTheDocument()
    expect(screen.getByText("issues: write")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /open its permissions page/i })).toHaveAttribute(
      "href",
      expect.stringContaining("/organizations/acme/settings/apps/clevis-acme/permissions"),
    )
  })

  it("links a personally owned App through the user's own settings", async () => {
    statusMock.mockResolvedValue(
      status({
        source: "env",
        configured: true,
        slug: "clevis",
        app_missing_permissions: { issues: "write" },
        app_owner_login: "me",
        app_owner_type: "User",
      }),
    )
    renderCard()
    const link = await screen.findByRole("link", { name: /open its permissions page/i })
    expect(link.getAttribute("href")).toMatch(/\/settings\/apps\/clevis\/permissions$/)
    expect(link.getAttribute("href")).not.toContain("/organizations/")
  })

  it("shows no warning when nothing is missing, and a quiet note when GitHub couldn't be asked", async () => {
    statusMock.mockResolvedValue(status({ configured: true, app_missing_permissions: {} }))
    const { unmount } = renderCard()
    await screen.findByText("No GitHub App is set up yet.")
    expect(screen.queryByText(/doesn.t request/i)).not.toBeInTheDocument()
    unmount()

    statusMock.mockResolvedValue(status({ configured: true, app_check_error: "Could not read the App's permissions from GitHub." }))
    renderCard()
    expect(await screen.findByText(/could not read the app.s permissions/i)).toBeInTheDocument()
  })
})

describe("GithubAppSetupCard", () => {
  it("creates the App by posting the manifest to GitHub", async () => {
    statusMock.mockResolvedValue(status())
    manifestMock.mockResolvedValue(manifest)
    renderCard()

    expect(await screen.findByText("No GitHub App is set up yet.")).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Organization to own the App"), { target: { value: " acme " } })
    fireEvent.click(screen.getByRole("button", { name: "Create GitHub App" }))

    await waitFor(() => expect(submitted).not.toBeNull())
    expect(manifestMock).toHaveBeenCalledWith({
      ui_origin: window.location.origin, organization: "acme", read_only: false, replace: false,
    })
    expect(submitted).toEqual({ action: manifest.post_url, manifest: JSON.stringify(manifest.manifest) })
  })

  it("offers a read-only choice", async () => {
    statusMock.mockResolvedValue(status())
    manifestMock.mockResolvedValue(manifest)
    renderCard()

    await screen.findByText("No GitHub App is set up yet.")
    fireEvent.click(screen.getByRole("checkbox", { name: /read-only/i }))
    fireEvent.click(screen.getByRole("button", { name: "Create GitHub App" }))

    await waitFor(() => expect(manifestMock).toHaveBeenCalledWith(expect.objectContaining({ read_only: true })))
  })

  it("explains an environment-configured App and offers no creation", async () => {
    statusMock.mockResolvedValue(status({ source: "env", configured: true, slug: "clevis-acme" }))
    renderCard()

    expect(await screen.findByText(/Configured through environment variables/)).toBeInTheDocument()
    expect(screen.getByText("clevis-acme")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Create GitHub App" })).not.toBeInTheDocument()
    expect(screen.getByText("administration: write")).toBeInTheDocument() // the by-hand list
    expect(screen.getByText(/Subscribe to events: push, member\./)).toBeInTheDocument()
  })

  it("needs an explicit confirmation to replace an App created here", async () => {
    statusMock.mockResolvedValue(status({ source: "database", configured: true, slug: "clevis-acme" }))
    manifestMock.mockResolvedValue(manifest)
    renderCard()

    const create = await screen.findByRole("button", { name: "Create GitHub App" })
    expect(create).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox", { name: /replace the existing app/i }))
    expect(create).toBeEnabled()
    fireEvent.click(create)

    await waitFor(() => expect(manifestMock).toHaveBeenCalledWith(expect.objectContaining({ replace: true })))
  })

  it("shows the API's refusal", async () => {
    statusMock.mockResolvedValue(status())
    manifestMock.mockRejectedValue(new Error("This UI address is not listed in CORS_ORIGINS"))
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: "Create GitHub App" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("not listed in CORS_ORIGINS")
    expect(submitted).toBeNull()
  })

  it("shows a load error with a retry", async () => {
    statusMock.mockRejectedValueOnce(new Error("nope")).mockResolvedValue(status())
    renderCard()

    expect(await screen.findByText("nope")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    expect(await screen.findByText("No GitHub App is set up yet.")).toBeInTheDocument()
  })

  it("falls back to a generic message when the load error has no message", async () => {
    statusMock.mockRejectedValue("boom")
    renderCard()
    expect(await screen.findByText("Couldn't load the GitHub App status.")).toBeInTheDocument()
  })

  it("falls back to a generic message when starting fails without a message", async () => {
    statusMock.mockResolvedValue(status())
    manifestMock.mockRejectedValue("boom")
    renderCard()
    fireEvent.click(await screen.findByRole("button", { name: "Create GitHub App" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't start the GitHub App setup.")
  })
})

describe("GithubAppCallbackPage", () => {
  it("exchanges GitHub's code exactly once and reports the new App", async () => {
    search = new URLSearchParams({ code: "abc", state: "S" })
    convertMock.mockResolvedValue({ slug: "clevis-acme", app_id: "4242" })
    render(<GithubAppCallbackPage />)

    expect(await screen.findByText("clevis-acme")).toBeInTheDocument()
    expect(convertMock).toHaveBeenCalledTimes(1)
    expect(convertMock).toHaveBeenCalledWith({ code: "abc", state: "S" })
    fireEvent.click(screen.getByRole("button", { name: "Back to Settings" }))
    expect(replaceMock).toHaveBeenCalledWith("/settings")
  })

  it("explains a missing code without calling the API", async () => {
    render(<GithubAppCallbackPage />)
    expect(await screen.findByText(/didn't send a setup code/)).toBeInTheDocument()
    expect(convertMock).not.toHaveBeenCalled()
  })

  it("shows the API's reason when the exchange fails", async () => {
    search = new URLSearchParams({ code: "abc", state: "S" })
    convertMock.mockRejectedValue(new Error("GitHub refused the setup code"))
    render(<GithubAppCallbackPage />)
    expect(await screen.findByText("GitHub refused the setup code")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Back to Settings" }))
    expect(replaceMock).toHaveBeenCalledWith("/settings")
  })

  it("falls back to a generic message for an error without a message", async () => {
    search = new URLSearchParams({ code: "abc", state: "S" })
    convertMock.mockRejectedValue("boom")
    render(<GithubAppCallbackPage />)
    expect(await screen.findByText("Couldn't finish creating the GitHub App.")).toBeInTheDocument()
  })
})
