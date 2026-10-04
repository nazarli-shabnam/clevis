import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listForOrgMock = vi.fn()
const invitationsListMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    installations: { list: vi.fn(), listForOrg: (...a: unknown[]) => listForOrgMock(...a) },
    invitations: { list: (...a: unknown[]) => invitationsListMock(...a) },
  },
}))

import { FirstRunChecklist } from "@/components/first-run-checklist"
import type { InstallationMeta } from "@/lib/api/types"

const SCOPE = { kind: "org", login: "acme" } as const

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

function renderChecklist(props: Partial<React.ComponentProps<typeof FirstRunChecklist>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <FirstRunChecklist scope={SCOPE} hasScan={true} canInvite={false} membersUrl="/settings/org/acme/members" {...props} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  listForOrgMock.mockReset()
  invitationsListMock.mockReset()
  localStorage.clear()
})
afterEach(cleanup)

describe("FirstRunChecklist", () => {
  it("asks to install the App when none is connected, and skips the permissions step", async () => {
    listForOrgMock.mockResolvedValue([])
    renderChecklist()

    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
    expect(screen.queryByText(/Grant the permissions/)).toBeNull()
    expect(screen.getByRole("link", { name: /Install the GitHub App/ })).toHaveAttribute("href", "/settings")
  })

  it("points at the Permissions page when an automation is blocked", async () => {
    listForOrgMock.mockResolvedValue([
      install({ blocked_features: [{ feature: "fix_this", label: "Fix this", missing: { administration: "write" } }] }),
    ])
    renderChecklist()

    expect(await screen.findByRole("link", { name: /Grant the permissions/ })).toHaveAttribute(
      "href",
      "/automation/permissions",
    )
  })

  it("treats never-synced permissions as not complete", async () => {
    listForOrgMock.mockResolvedValue([install({ permissions_synced_at: null })])
    renderChecklist()

    expect(await screen.findByText(/Grant the permissions/)).toBeInTheDocument()
  })

  it("lists the scan step only until a scan exists, and waits while that is unknown", async () => {
    listForOrgMock.mockResolvedValue([install()])
    const { unmount } = renderChecklist({ hasScan: false })
    expect(await screen.findByText("Run your first security scan")).toBeInTheDocument()
    unmount()

    const { container } = renderChecklist({ hasScan: null })
    await waitFor(() => expect(listForOrgMock).toHaveBeenCalledTimes(2))
    expect(container).toBeEmptyDOMElement()
  })

  it("offers the invite step to org admins with no invitations", async () => {
    listForOrgMock.mockResolvedValue([install()])
    invitationsListMock.mockResolvedValue([])
    renderChecklist({ canInvite: true })

    expect(await screen.findByRole("link", { name: /Invite a teammate/ })).toHaveAttribute(
      "href",
      "/settings/org/acme/members",
    )
  })

  it("renders nothing when everything is complete", async () => {
    listForOrgMock.mockResolvedValue([install()])
    invitationsListMock.mockResolvedValue([{ id: 1 }])
    const { container } = renderChecklist({ canInvite: true })

    await waitFor(() => expect(invitationsListMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("renders nothing when the installation lookup fails", async () => {
    listForOrgMock.mockRejectedValue(new Error("403"))
    const { container } = renderChecklist()

    await waitFor(() => expect(listForOrgMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it("can be dismissed, and stays dismissed", async () => {
    listForOrgMock.mockResolvedValue([])
    const { unmount } = renderChecklist()

    fireEvent.click(await screen.findByRole("button", { name: "Dismiss getting started" }))
    expect(screen.queryByText("Install the GitHub App")).toBeNull()
    unmount()

    const { container } = renderChecklist()
    expect(container).toBeEmptyDOMElement()
  })

  it("still shows the checklist when localStorage is unavailable", async () => {
    listForOrgMock.mockResolvedValue([])
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    renderChecklist()

    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
    expect(screen.getByText("Getting started — 1 step left")).toBeInTheDocument()
    spy.mockRestore()
  })
})
