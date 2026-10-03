import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listForOrgMock = vi.fn()
const listMock = vi.fn()
const refreshMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    installations: {
      list: (...args: unknown[]) => listMock(...args),
      listForOrg: (...args: unknown[]) => listForOrgMock(...args),
      refreshPermissions: (...args: unknown[]) => refreshMock(...args),
    },
  },
}))

import PermissionsPage from "@/app/automation/permissions/page"
import type { InstallationMeta } from "@/lib/api/types"

function install(overrides: Partial<InstallationMeta> = {}): InstallationMeta {
  return {
    id: 1,
    account_login: "acme",
    account_type: "Organization",
    installation_id: 42,
    created_at: "2026-01-01T00:00:00Z",
    permissions_synced_at: "2026-09-01T00:00:00Z",
    blocked_features: [],
    automations: [
      { feature: "workflow_dispatch", label: "Workflow dispatch (Automation page)", required: { actions: "write" }, missing: {} },
      { feature: "fix_this", label: "Fix this auto-remediation", required: { administration: "write" }, missing: { administration: "write" } },
    ],
    ...overrides,
  }
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <PermissionsPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  listForOrgMock.mockReset()
  listMock.mockReset()
  refreshMock.mockReset()
  localStorage.setItem("active_scope", JSON.stringify({ kind: "org", login: "acme" }))
  vi.stubEnv("NEXT_PUBLIC_GITHUB_APP_SLUG", "clevis")
})

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.unstubAllEnvs()
})

describe("PermissionsPage", () => {
  it("lists every automation with its requirement and ready/missing status", async () => {
    listForOrgMock.mockResolvedValue([install()])
    renderPage()

    await waitFor(() => expect(screen.getByText("Workflow dispatch (Automation page)")).toBeInTheDocument())
    expect(listForOrgMock).toHaveBeenCalledWith("acme")
    expect(screen.getByText("Ready")).toBeInTheDocument()
    expect(screen.getByText("Missing administration: write")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Review on GitHub/ })).toHaveAttribute(
      "href",
      expect.stringContaining("/apps/clevis/installations/42"),
    )
  })

  it("shows status as unknown when permissions were never synced", async () => {
    listForOrgMock.mockResolvedValue([install({ permissions_synced_at: null })])
    renderPage()

    await waitFor(() => expect(screen.getByText("Permissions not yet checked")).toBeInTheDocument())
    expect(screen.getAllByText("Unknown")).toHaveLength(2)
    expect(screen.queryByText("Ready")).toBeNull()
  })

  it("re-syncs permissions for the installation and refetches", async () => {
    listForOrgMock.mockResolvedValue([install()])
    refreshMock.mockResolvedValue(install())
    renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "Re-sync permissions" }))

    await waitFor(() => expect(refreshMock).toHaveBeenCalledWith({ scope: "org", orgLogin: "acme" }, 42))
    await waitFor(() => expect(listForOrgMock).toHaveBeenCalledTimes(2))
  })

  it("surfaces a re-sync failure", async () => {
    listForOrgMock.mockResolvedValue([install()])
    refreshMock.mockRejectedValue(new Error("GitHub API unreachable"))
    renderPage()

    fireEvent.click(await screen.findByRole("button", { name: "Re-sync permissions" }))

    expect(await screen.findByText("GitHub API unreachable")).toBeInTheDocument()
  })

  it("uses the personal installations endpoint for a personal scope", async () => {
    localStorage.setItem("active_scope", JSON.stringify({ kind: "personal", login: "me" }))
    listMock.mockResolvedValue([install({ account_type: "User", account_login: "me" })])
    renderPage()

    await waitFor(() => expect(listMock).toHaveBeenCalled())
    expect(listForOrgMock).not.toHaveBeenCalled()
  })

  it("explains when no installation is connected", async () => {
    listForOrgMock.mockResolvedValue([])
    renderPage()

    expect(await screen.findByText(/No GitHub App installation is connected for acme/)).toBeInTheDocument()
  })
})
