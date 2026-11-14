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
    expect(screen.getByText(/Webhook · signed · paused/)).toBeInTheDocument()
    expect(screen.getAllByText(/fire at 10\+ points/)).toHaveLength(2)
  })

  it("adds a destination, then clears the credential fields", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue(dest())
    renderCard()
    await screen.findByText(/No destinations yet/)
    fireEvent.click(screen.getByRole("button", { name: "Add a destination" }))
    expect(screen.getByLabelText("Name")).toHaveFocus()
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
    await screen.findByText(/No destinations yet/)
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
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("acme", 1, { add_events: ["automation_failed"] }))
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

  it("shows a load error and retries it", async () => {
    listMock.mockRejectedValueOnce(new Error("boom"))
    listMock.mockResolvedValue([dest()])
    renderCard()
    expect(await screen.findByText(/Couldn't load destinations: boom/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByText("#alerts")).toBeInTheDocument()
  })

  it("lists an email destination's recipients", async () => {
    listMock.mockResolvedValue([dest({ kind: "email", recipients: ["a@e.com", "b@e.com"] })])
    renderCard()
    expect(await screen.findByText("a@e.com, b@e.com")).toBeInTheDocument()
  })

  it("sends the signing secret only for a generic webhook", async () => {
    listMock.mockResolvedValue([])
    createMock.mockResolvedValue(dest({ kind: "generic" }))
    renderCard()
    await screen.findByText(/No destinations yet/)
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "generic" } })
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "hook" } })
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://example.com/h" } })
    fireEvent.change(screen.getByLabelText("Signing secret (optional)"), { target: { value: "s3cret" } })
    fireEvent.change(screen.getByLabelText("Score-drop threshold (points)"), { target: { value: "20" } })
    fireEvent.click(screen.getByRole("button", { name: "Add destination" }))
    await waitFor(() => expect(createMock).toHaveBeenCalled())
    expect(createMock.mock.calls[0][1]).toMatchObject({ kind: "generic", url: "https://example.com/h", secret: "s3cret", min_score_drop: 20 })
    await waitFor(() => expect(screen.getByLabelText("Signing secret (optional)")).toHaveValue(""))
  })

  it("reports a failed update, test-send and delete on the row", async () => {
    listMock.mockResolvedValue([dest()])
    updateMock.mockRejectedValue(new Error("nope"))
    testMock.mockRejectedValue(new Error("unreachable"))
    removeMock.mockRejectedValue(new Error("locked"))
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByLabelText("Enabled: #alerts"))
    expect(await screen.findByText(/Couldn't update: nope/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Send a test to #alerts" }))
    expect(await screen.findByText(/Test failed: unreachable/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Delete #alerts" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    expect(await screen.findByText(/Couldn't delete: locked/)).toBeInTheDocument()
  })

  it("shows a successful test-send result", async () => {
    listMock.mockResolvedValue([dest()])
    testMock.mockResolvedValue({ ok: true, detail: "Delivered" })
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByRole("button", { name: "Send a test to #alerts" }))
    expect(await screen.findByText(/Delivered/)).toBeInTheDocument()
  })

  it("keeps the rows when a background refetch fails", async () => {
    listMock.mockResolvedValueOnce([dest()]).mockRejectedValue(new Error("later"))
    updateMock.mockResolvedValue(dest({ enabled: false }))
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByLabelText("Enabled: #alerts"))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    expect(screen.getByText("#alerts")).toBeInTheDocument()
    expect(screen.queryByText(/Couldn't load destinations/)).not.toBeInTheDocument()
  })

  it("reloads the list after a failed update or delete, so the row never shows stale state", async () => {
    listMock.mockResolvedValue([dest()])
    updateMock.mockRejectedValue(new Error("lost"))
    removeMock.mockRejectedValue(new Error("lost"))
    renderCard()
    await screen.findByText("#alerts")
    fireEvent.click(screen.getByLabelText("Enabled: #alerts"))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
    fireEvent.click(screen.getByRole("button", { name: "Delete #alerts" }))
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }))
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(3))
  })

  it("keeps Add disabled for an email destination whose recipients parse to nothing", async () => {
    listMock.mockResolvedValue([])
    renderCard()
    await screen.findByText(/No destinations yet/)
    fireEvent.change(screen.getByLabelText("Type"), { target: { value: "email" } })
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "mail" } })
    fireEvent.change(screen.getByLabelText("Recipients"), { target: { value: " , ," } })
    expect(screen.getByRole("button", { name: "Add destination" })).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Recipients"), { target: { value: "a@e.com" } })
    expect(screen.getByRole("button", { name: "Add destination" })).toBeEnabled()
  })

  it("reloads the list after a failed create, so a create whose response was lost shows up before a retry", async () => {
    listMock.mockResolvedValueOnce([]).mockResolvedValue([dest({ name: "#sec" })])
    createMock.mockRejectedValue(new Error("network"))
    renderCard()
    await screen.findByText(/No destinations yet/)
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "#sec" } })
    fireEvent.change(screen.getByLabelText("Webhook URL"), { target: { value: "https://hooks.slack.com/x" } })
    fireEvent.click(screen.getByRole("button", { name: "Add destination" }))
    expect(await screen.findByText("#sec", { selector: "p, span" })).toBeInTheDocument()
    expect(listMock).toHaveBeenCalledTimes(2)
  })

  describe("editing in place (#950)", () => {
    const openEdit = async (d: Record<string, unknown> = {}) => {
      listMock.mockResolvedValue([dest(d)])
      renderCard()
      fireEvent.click(await screen.findByRole("button", { name: /^Edit / }))
      return screen.getByRole("form", { name: /^Edit / })
    }

    it("sends only the changed fields and leaves the write-only URL alone when blank", async () => {
      updateMock.mockResolvedValue(dest())
      const form = await openEdit()
      const save = within(form).getByRole("button", { name: "Save changes" })
      expect(save).toBeDisabled()
      fireEvent.change(within(form).getByLabelText("Name"), { target: { value: " #renamed " } })
      fireEvent.change(within(form).getByLabelText("Score-drop threshold (points)"), { target: { value: "25" } })
      fireEvent.click(save)
      await waitFor(() => expect(updateMock).toHaveBeenCalledWith("acme", 1, { name: "#renamed", min_score_drop: 25 }))
      await waitFor(() => expect(screen.queryByRole("form", { name: /^Edit / })).not.toBeInTheDocument())
    })

    it("rotates the URL and the signing secret of a webhook, never prefilled", async () => {
      updateMock.mockResolvedValue(dest())
      const form = await openEdit({ kind: "generic", signed: true })
      expect(within(form).getByLabelText("New webhook URL")).toHaveValue("")
      expect(within(form).getByLabelText("New signing secret")).toHaveValue("")
      fireEvent.change(within(form).getByLabelText("New webhook URL"), { target: { value: " https://hooks.example/new " } })
      fireEvent.change(within(form).getByLabelText("New signing secret"), { target: { value: "s3cret" } })
      fireEvent.click(within(form).getByRole("button", { name: "Save changes" }))
      await waitFor(() =>
        expect(updateMock).toHaveBeenCalledWith("acme", 1, { url: "https://hooks.example/new", secret: "s3cret" }),
      )
    })

    it("edits an email destination's recipients and rejects an empty list", async () => {
      updateMock.mockResolvedValue(dest())
      const form = await openEdit({ kind: "email", recipients: ["a@e.com", "b@e.com"] })
      const recipients = within(form).getByLabelText("Recipients")
      expect(recipients).toHaveValue("a@e.com, b@e.com")
      expect(within(form).queryByLabelText("New webhook URL")).not.toBeInTheDocument()
      fireEvent.change(recipients, { target: { value: " , " } })
      expect(within(form).getByRole("button", { name: "Save changes" })).toBeDisabled()
      fireEvent.change(recipients, { target: { value: "a@e.com c@e.com" } })
      fireEvent.click(within(form).getByRole("button", { name: "Save changes" }))
      await waitFor(() => expect(updateMock).toHaveBeenCalledWith("acme", 1, { recipients: ["a@e.com", "c@e.com"] }))
    })

    it("blocks an out-of-range threshold or empty name", async () => {
      const form = await openEdit()
      const save = within(form).getByRole("button", { name: "Save changes" })
      fireEvent.change(within(form).getByLabelText("Score-drop threshold (points)"), { target: { value: "0" } })
      expect(save).toBeDisabled()
      fireEvent.change(within(form).getByLabelText("Score-drop threshold (points)"), { target: { value: "20" } })
      expect(save).toBeEnabled()
      fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "  " } })
      expect(save).toBeDisabled()
    })

    it("keeps the form open with the server's error when the save fails, and Cancel closes it", async () => {
      updateMock.mockRejectedValue(new Error("Recipient x@e.com is not a verified member"))
      const form = await openEdit()
      fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "other" } })
      fireEvent.click(within(form).getByRole("button", { name: "Save changes" }))
      expect(await within(form).findByRole("alert")).toHaveTextContent("not a verified member")
      expect(screen.getAllByRole("alert")).toHaveLength(1)
      fireEvent.click(within(form).getByRole("button", { name: "Cancel" }))
      expect(screen.queryByRole("form", { name: /^Edit / })).not.toBeInTheDocument()
    })
  })
})
