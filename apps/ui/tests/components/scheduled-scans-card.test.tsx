import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getMock = vi.fn()
const setMock = vi.fn()
const changesMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: {
      scheduledScans: (...a: unknown[]) => getMock(...a),
      setScheduledScans: (...a: unknown[]) => setMock(...a),
    },
    analytics: { changes: (...a: unknown[]) => changesMock(...a) },
  },
}))

import { ScheduledScansCard } from "@/components/scheduled-scans-card"
import { ScanChangesCard } from "@/components/scan-changes-card"

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  getMock.mockReset()
  setMock.mockReset()
  changesMock.mockReset()
})
afterEach(cleanup)

describe("ScheduledScansCard", () => {
  it("shows the instance cadence and whether the org is scanned", async () => {
    getMock.mockResolvedValue({ enabled: null, effective: false, cadence: null, instance_cadence: "off" })
    wrap(<ScheduledScansCard orgLogin="acme" />)
    expect(await screen.findByRole("option", { name: "Follow the instance setting (off)" })).toBeInTheDocument()
    expect(screen.getByLabelText(/Scan this organization automatically/)).toHaveValue("follow")
    expect(screen.getByText(/Not scanned automatically/)).toBeInTheDocument()
  })

  it("saves 'always' as true, 'never' as false and 'follow' as null", async () => {
    getMock.mockResolvedValue({ enabled: null, effective: false, cadence: null, instance_cadence: "off" })
    setMock.mockResolvedValue({ enabled: true, effective: true, cadence: "weekly", instance_cadence: "off" })
    wrap(<ScheduledScansCard orgLogin="acme" />)
    const select = await screen.findByLabelText(/Scan this organization automatically/)

    fireEvent.change(select, { target: { value: "on" } })
    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", true))
    await waitFor(() => expect(screen.getByText(/Re-scanned weekly/)).toBeInTheDocument())

    setMock.mockResolvedValue({ enabled: false, effective: false, cadence: null, instance_cadence: "off" })
    fireEvent.change(select, { target: { value: "off" } })
    await waitFor(() => expect(setMock).toHaveBeenLastCalledWith("acme", false))

    fireEvent.change(select, { target: { value: "follow" } })
    await waitFor(() => expect(setMock).toHaveBeenLastCalledWith("acme", null))
  })

  it("shows a retry instead of controls when settings fail to load", async () => {
    getMock.mockRejectedValue(new Error("forbidden"))
    wrap(<ScheduledScansCard orgLogin="acme" />)
    await waitFor(() => expect(screen.getByText(/Couldn't load the scheduled-scan settings: forbidden/)).toBeInTheDocument())
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  })
})

const changes = (over: Record<string, unknown> = {}) => ({
  org: "acme",
  has_previous: true,
  previous_scanned_at: "2026-01-01T00:00:00Z",
  scanned_at: "2026-01-02T00:00:00Z",
  previous_score: 80,
  score: 60,
  comparable: true,
  changes: [
    { id: "a", title: "Branch protection", severity: "high", change: "newly_failing", previous_status: "pass", status: "fail", repos: ["api", "web", "r3", "r4", "r5", "r6", "r7"] },
    { id: "b", title: "Secret scanning", severity: "medium", change: "newly_passing", previous_status: "fail", status: "pass", repos: [] },
  ],
  ...over,
})

describe("ScanChangesCard", () => {
  it("lists what changed with the score move and the affected repos, capped", async () => {
    changesMock.mockResolvedValue(changes())
    wrap(<ScanChangesCard org="acme" />)
    expect(await screen.findByText("Branch protection")).toBeInTheDocument()
    expect(screen.getByText("80 → 60")).toBeInTheDocument()
    expect(screen.getByText("api, web, r3, r4, r5 and 2 more")).toBeInTheDocument()
    expect(screen.getByText("Now failing")).toBeInTheDocument()
    expect(screen.getByText("Fixed")).toBeInTheDocument()
  })

  it("warns when the two scans are not comparable", async () => {
    changesMock.mockResolvedValue(changes({ comparable: false }))
    wrap(<ScanChangesCard org="acme" />)
    expect(await screen.findByText("80 → 60 (not comparable)")).toBeInTheDocument()
    expect(screen.getByText(/may not reflect a real change/)).toBeInTheDocument()
  })

  it("says so when nothing changed, and when there is no earlier scan", async () => {
    changesMock.mockResolvedValue(changes({ changes: [] }))
    const { unmount } = wrap(<ScanChangesCard org="acme" />)
    expect(await screen.findByText("No check changed status.")).toBeInTheDocument()
    unmount()

    changesMock.mockResolvedValue({ org: "acme", has_previous: false, scanned_at: "2026-01-02T00:00:00Z", score: 90, changes: [], comparable: true })
    wrap(<ScanChangesCard org="acme" />)
    expect(await screen.findByText(/Only one scan so far/)).toBeInTheDocument()
  })

  it("shows an error with retry when the comparison fails", async () => {
    changesMock.mockRejectedValue(new Error("boom"))
    wrap(<ScanChangesCard org="acme" />)
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument())
  })
})
