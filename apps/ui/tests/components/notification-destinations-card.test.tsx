import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const listMock = vi.fn()
const createMock = vi.fn()
const updateMock = vi.fn()
const removeMock = vi.fn()
const testMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    notificationDestinations: {
      list: (...a: unknown[]) => listMock(...a),
      create: (...a: unknown[]) => createMock(...a),
      update: (...a: unknown[]) => updateMock(...a),
      remove: (...a: unknown[]) => removeMock(...a),
      test: (...a: unknown[]) => testMock(...a),
    },
  },
}))

import { NotificationDestinationsCard } from "@/components/notification-destinations-card"

const dest = (over: Record<string, unknown> = {}) => ({
  id: 1,
  kind: "slack",
  name: "#alerts",
  events: ["score_drop"],
  min_score_drop: 10,
  enabled: true,
  created_at: new Date().toISOString(),
  signed: false,
  recipients: null,
  ...over,
})

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <NotificationDestinationsCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  for (const m of [listMock, createMock, updateMock, removeMock, testMock]) m.mockReset()
})
afterEach(cleanup)

describe("NotificationDestinationsCard", () => {
  it("lists destinations with kind, state and threshold, never a URL", async () => {
    listMock.mockResolvedValue([dest(), dest({ id: 2, name: "ops", kind: "generic", signed: true, enabled: false })])
    renderCard()
    expect(await screen.findByText("#alerts")).toBeInTheDocument()
    expect(screen.getByText(/Signed webhook · signed · paused/)).toBeInTheDocument()
    expect(screen.getAllByText(/fire at 10\+ points/)).toHaveLength(2)
  })

  it("adds a destination, then clears the credential fields", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue(dest())
    renderCard()
    await screen.findByText("No destinations.")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " #sec " } })
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: " https://hooks.slack.com/x " } })
    fireEvent.click(screen.getByLabelText("New critical/high alert"))
    fireEvent.click(screen.getByRole("button", { name: "Add destination" }))
    await waitFor(() => expect(createMock).toHaveBeenCalled())
    expect(createMock).toHaveBeenCalledWith("acme", {
      kind: "slack",
      name: "#sec",
      url: "https://hooks.slack.com/x",
      events: ["score_drop", "new_critical_alert"],
      min_score_drop: 10,
    })
    await waitFor(() => expect(screen.getByLabelText("Webhook URL")).toHaveValue(""))
  })

  it("sends recipients for an email destination and shows the API's validation message", async () => {
    listMock.mockResolvedValue([])
    createMock.mockRejectedValue(new Error("Recipients must be verified members of this organization: x@e.com"))
    renderCard()
    await screen.findByText("No destinations.")
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "email" } })
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "mail" } })
    fireEvent.change(screen.getByLabelText("Recipients"), { target: { value: "x@e.com, y@e.com" } })
    fireEvent.click(screen.getByRole("button", { name: "Add destination" }))
    expect(await screen.findByText(/Recipients must be verified members/)).toBeInTheDocument()
    expect(createMock.mock.calls[0][1].recipients).toEqual(["x@e.com", "y@e.com"])
  })

  it("toggles enabled and events through update", async () => {
    listMock.mockResolvedValue([dest()])
    updateMock.mockResolvedValue(dest({ enabled: false }))
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByLabelText("Enabled: #alerts"))
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("acme", 1, { enabled: false }))
    const events = screen.getByRole("group", { name: "Events for #alerts" })
    fireEvent.click(within(events).getByLabelText("Automation failed"))
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("acme", 1, { events: ["score_drop", "automation_failed"] }))
  })

  it("shows the test-send result on its own row", async () => {
    listMock.mockResolvedValue([dest(), dest({ id: 2, name: "other" })])
    testMock.mockResolvedValue({ ok: false, detail: "Delivery failed; see the audit log" })
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByRole("button", { name: "Send a test to #alerts" }))
    expect(await screen.findByText(/Delivery failed; see the audit log/)).toBeInTheDocument()
    expect(testMock).toHaveBeenCalledWith("acme", 1)
    expect(screen.getAllByRole("status")).toHaveLength(1)
  })

  it("deletes only after confirming", async () => {
    listMock.mockResolvedValue([dest()])
    removeMock.mockResolvedValue(undefined)
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByRole("button", { name: "Delete #alerts" }))
    expect(removeMock).not.toHaveBeenCalled()
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    await waitFor(() => expect(removeMock).toHaveBeenCalledWith("acme", 1))
  })
})
