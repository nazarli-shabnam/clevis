import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const meMock = vi.fn()
const linkMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { auth: { me: (...a: unknown[]) => meMock(...a), linkGithub: (...a: unknown[]) => linkMock(...a) } },
}))

import { GithubLinkField } from "@/components/github-link-field"

function renderField() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <GithubLinkField />
    </QueryClientProvider>,
  )
}

describe("GithubLinkField", () => {
  beforeEach(() => {
    meMock.mockReset()
    linkMock.mockReset()
    // jsdom throws on a real navigation; make href a plain writable property.
    Object.defineProperty(window, "location", { writable: true, value: { href: "" } })
  })
  afterEach(cleanup)

  it("shows the linked account and offers nothing to change", async () => {
    meMock.mockResolvedValue({ id: 1, github_login: "octo" })
    renderField()
    expect(await screen.findByText("@octo")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Link GitHub account/ })).not.toBeInTheDocument()
  })

  it("starts linking and opens the GitHub URL the API returns", async () => {
    meMock.mockResolvedValue({ id: 1, github_login: null })
    linkMock.mockResolvedValue({ url: "https://github.com/login/oauth/authorize?state=s" })
    renderField()
    fireEvent.click(await screen.findByRole("button", { name: /Link GitHub account/ }))
    await waitFor(() => expect(window.location.href).toBe("https://github.com/login/oauth/authorize?state=s"))
    expect(screen.getByRole("button", { name: /Opening GitHub/ })).toBeDisabled()
  })

  it("shows why linking could not start and lets the user retry", async () => {
    meMock.mockResolvedValue({ id: 1, github_login: null })
    linkMock.mockRejectedValue(new Error("GitHub sign-in isn't set up on this server yet"))
    renderField()
    fireEvent.click(await screen.findByRole("button", { name: /Link GitHub account/ }))
    expect(await screen.findByRole("alert")).toHaveTextContent("isn't set up on this server")
    expect(screen.getByRole("button", { name: /Link GitHub account/ })).toBeEnabled()
  })

  it("renders nothing until the profile is known, and nothing if it cannot be loaded", async () => {
    meMock.mockRejectedValue(new Error("down"))
    const { container } = renderField()
    await waitFor(() => expect(meMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
