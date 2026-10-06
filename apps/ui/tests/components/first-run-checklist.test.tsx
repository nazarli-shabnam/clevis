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
import { clearDismissals, dismissalKey } from "@/lib/first-run-dismissal"
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
  const tree = (extra: typeof props) => (
    <QueryClientProvider client={queryClient}>
      <FirstRunChecklist scope={SCOPE} userId={1} hasScan={true} canInvite={false} membersUrl="/settings/org/acme/members" {...props} {...extra} />
    </QueryClientProvider>
  )
  const utils = render(tree({}))
  return { ...utils, update: (extra: typeof props) => utils.rerender(tree(extra)) }
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

  it("says so, with a retry, when the installation lookup fails", async () => {
    listForOrgMock.mockRejectedValueOnce(new Error("403")).mockResolvedValueOnce([])
    renderChecklist()

    expect(await screen.findByText("Couldn't load your setup progress.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))

    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
  })

  it("explains why it is missing when the cockpit failed to load, without a second Retry", async () => {
    listForOrgMock.mockResolvedValue([install()])
    renderChecklist({ hasScan: null, scanFailed: true })

    expect(await screen.findByText(/checklist will appear once the Overview data loads/)).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull()
  })

  it("renders nothing until the user id is known, since a dismissal could not be remembered", async () => {
    listForOrgMock.mockResolvedValue([install()])
    const { container } = renderChecklist({ userId: null, hasOrg: false })

    await new Promise((r) => setTimeout(r, 30))
    expect(container).toBeEmptyDOMElement()
  })

  it("points at the error above when the cockpit failed", async () => {
    listForOrgMock.mockResolvedValue([install()])
    renderChecklist({ hasScan: null, scanFailed: true })

    expect(await screen.findByText(/Retry it from the error above\./)).toBeInTheDocument()
  })

  it("offers to connect an org only for a user who is in none, and waits while that is unknown", async () => {
    listForOrgMock.mockResolvedValue([install()])
    const { unmount } = renderChecklist({ hasOrg: false })
    expect(await screen.findByRole("link", { name: /Connect an organization/ })).toHaveAttribute("href", "/settings")
    unmount()

    for (const hasOrg of [true, null]) {
      const { container, unmount: u } = renderChecklist({ hasOrg })
      await waitFor(() => expect(listForOrgMock).toHaveBeenCalled())
      expect(container).toBeEmptyDOMElement()
      u()
    }
  })

  it("lists the automation step only when it is known that none has run", async () => {
    listForOrgMock.mockResolvedValue([install()])
    const { unmount } = renderChecklist({ hasAutomationRun: false })
    expect(await screen.findByRole("link", { name: /Run your first automation/ })).toHaveAttribute("href", "/automation")
    unmount()

    for (const hasAutomationRun of [true, null]) {
      const { container, unmount: u } = renderChecklist({ hasAutomationRun })
      await waitFor(() => expect(listForOrgMock).toHaveBeenCalled())
      expect(container).toBeEmptyDOMElement()
      u()
    }
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

  it("keeps a dismissal to the user and account that made it", async () => {
    listForOrgMock.mockResolvedValue([])
    const first = renderChecklist()
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss getting started" }))
    first.unmount()

    // another user, same browser and org
    const otherUser = renderChecklist({ userId: 2 })
    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
    otherUser.unmount()
    // same user, another org
    const otherOrg = renderChecklist({ scope: { kind: "org", login: "globex" } })
    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
    otherOrg.unmount()
    // same user, same org still dismissed
    expect(renderChecklist().container).toBeEmptyDOMElement()
  })

  it("is not hidden by the old un-scoped dismissal key", async () => {
    localStorage.setItem("clevis:first-run-checklist-dismissed", "1")
    listForOrgMock.mockResolvedValue([])
    renderChecklist()

    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
  })

  it("re-reads the dismissal when the active account changes", async () => {
    listForOrgMock.mockResolvedValue([])
    localStorage.setItem(dismissalKey(1, "org", "acme"), "1")
    const { container, update } = renderChecklist()
    expect(container).toBeEmptyDOMElement()

    update({ scope: { kind: "org", login: "globex" } })
    expect(await screen.findByText("Install the GitHub App")).toBeInTheDocument()
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

describe("clearDismissals", () => {
  it("removes the legacy and every scoped dismissal but leaves other keys", () => {
    localStorage.setItem("clevis:first-run-checklist-dismissed", "1")
    localStorage.setItem(dismissalKey(1, "org", "acme"), "1")
    localStorage.setItem(dismissalKey(2, "personal", "me"), "1")
    localStorage.setItem("unrelated", "keep")

    clearDismissals()

    expect(localStorage.length).toBe(1)
    expect(localStorage.getItem("unrelated")).toBe("keep")
  })
})
