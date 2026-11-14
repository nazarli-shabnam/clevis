import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react"
import type { ReactElement } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { PermissionDriftNotice } from "@/components/permission-drift-notice"
import type { InstallationMeta } from "@/lib/api/types"

// The notice uses react-query for its "check now" refresh, so it needs a provider.
function render(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

function install(overrides: Partial<InstallationMeta> = {}): InstallationMeta {
  return {
    id: 1,
    account_login: "acme",
    account_type: "Organization",
    installation_id: 42,
    created_at: "2026-01-01T00:00:00Z",
    permissions_synced_at: "2026-09-01T00:00:00Z",
    blocked_features: [],
    ...overrides,
  }
}

beforeEach(() => vi.stubEnv("NEXT_PUBLIC_GITHUB_APP_SLUG", "clevis"))
afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

describe("PermissionDriftNotice", () => {
  it("renders nothing when the install is fully permissioned", () => {
    const { container } = render(<PermissionDriftNotice install={install()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("lists blocked feature labels and a Review on GitHub link", () => {
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
            { feature: "bulk_branch_protection", label: "Bulk branch-protection apply", missing: { administration: "write" } },
          ],
        })}
      />,
    )
    expect(screen.getByText(/2 automations need extra GitHub access/i)).toBeInTheDocument()
    expect(screen.getByText("Stale pull-request nudges")).toBeInTheDocument()
    const link = screen.getByRole("link", { name: /approve on github/i })
    expect(link).toHaveAttribute(
      "href",
      expect.stringContaining("/organizations/acme/settings/installations/42/permissions/update"),
    )
  })

  it("shows a 'not yet checked' line when permissions have never been observed", () => {
    render(<PermissionDriftNotice install={install({ permissions_synced_at: null })} />)
    expect(screen.getByText(/not yet checked/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /check now/i })).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })

  it("uses the singular 'automation' for exactly one blocked feature", () => {
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    expect(screen.getByText(/1 automation needs extra GitHub access/i)).toBeInTheDocument()
  })

  it("links personal accounts to their own settings page, with no App slug needed", () => {
    vi.unstubAllEnvs()
    render(
      <PermissionDriftNotice
        install={install({
          account_type: "User",
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    const link = screen.getByRole("link", { name: /approve on github/i })
    expect(link.getAttribute("href")).toMatch(/\/settings\/installations\/42\/permissions\/update$/)
    expect(link.getAttribute("href")).not.toContain("/organizations/")
  })

  it("shows the plain-language reason for each blocked automation", () => {
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
          automations: [
            {
              feature: "stale_pr_nudges",
              label: "Stale pull-request nudges",
              required: { pull_requests: "write" },
              description: "Lets Clevis comment on pull requests that have sat idle.",
              missing: { pull_requests: "write" },
            },
          ],
        })}
      />,
    )
    expect(screen.getByText(/comment on pull requests that have sat idle/i)).toBeInTheDocument()
  })

  it("copies a request for the owner containing the approval link", async () => {
    const writeText = vi.fn(async () => {})
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /copy request for owner/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect((writeText.mock.calls as unknown as string[][])[0][0]).toContain(
      "/organizations/acme/settings/installations/42/permissions/update",
    )
  })

  it("'Check now' re-reads a never-checked personal account through the /me endpoint", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(install()), { status: 200, headers: { "Content-Type": "application/json" } }),
    )
    vi.stubGlobal("fetch", fetchMock)
    render(<PermissionDriftNotice install={install({ account_type: "User", permissions_synced_at: null })} />)
    fireEvent.click(screen.getByRole("button", { name: /check now/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(String((fetchMock.mock.calls as unknown as string[][])[0][0])).toContain(
      "/me/installations/42/refresh-permissions",
    )
  })

  it("shows the error when re-checking fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "GitHub is unreachable" }), {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /i approved it/i }))
    expect(await screen.findByText(/github is unreachable/i)).toBeInTheDocument()
  })

  it("keeps the copy button usable when the clipboard is blocked", async () => {
    const writeText = vi.fn(async () => {
      throw new Error("denied")
    })
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: /copy request for owner/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(screen.getByRole("button", { name: /copy request for owner/i })).toBeInTheDocument()
  })

  it("offers no link or check button for an install without a GitHub installation id", () => {
    render(
      <PermissionDriftNotice
        install={install({
          installation_id: null,
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /check now/i })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /copy request for owner/i })).toBeInTheDocument()
  })

  it("re-checks permissions when the user returns from GitHub", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(install()), { status: 200, headers: { "Content-Type": "application/json" } }),
    )
    vi.stubGlobal("fetch", fetchMock)
    render(
      <PermissionDriftNotice
        install={install({
          blocked_features: [
            { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
          ],
        })}
      />,
    )
    fireEvent.click(screen.getByRole("link", { name: /approve on github/i }))
    window.dispatchEvent(new Event("focus"))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(String((fetchMock.mock.calls as unknown as string[][])[0][0])).toContain(
      "/orgs/acme/installations/42/refresh-permissions",
    )
  })
})
