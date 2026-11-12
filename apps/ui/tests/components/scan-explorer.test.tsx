import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const exportMock = vi.fn()
vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  api: { analytics: { exportHistory: (...a: unknown[]) => exportMock(...a) } },
}))

import { ScanExplorer } from "@/components/scan-explorer"

const scan = (id: number, created_at: string, score: number, checks: object[]) => ({
  id,
  owner: "acme",
  score,
  total_checks: checks.length,
  failed_checks: checks.filter((c) => (c as { status: string }).status === "fail").length,
  created_at,
  checks,
})

const NEWER = scan(2, "2026-11-03T10:00:00Z", 66, [
  { id: "mfa", title: "MFA required", severity: "high", status: "fail", value: "2 members without MFA" },
  { id: "bp", title: "Branch protection", severity: "high", status: "pass", value: null },
])
const OLDER = scan(1, "2026-10-20T10:00:00Z", 83, [
  { id: "mfa", title: "MFA required", severity: "high", status: "pass", value: null },
  { id: "bp", title: "Branch protection", severity: "high", status: "fail", value: { checked: 5, protected: 3 } },
])

function renderExplorer(scanId: number | null, compareId: number | null, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ScanExplorer owner="acme" scanId={scanId} compareId={compareId} onChange={onChange} />
    </QueryClientProvider>,
  )
  return onChange
}

beforeEach(() => {
  exportMock.mockReset()
  exportMock.mockResolvedValue({ truncated: false, row_count: 2, entries: [NEWER, OLDER] })
})
afterEach(() => cleanup())

describe("ScanExplorer", () => {
  it("lists scans and opens one on View", async () => {
    const onChange = renderExplorer(null, null)

    const views = await screen.findAllByRole("button", { name: /View scan from/ })
    expect(views).toHaveLength(2)
    fireEvent.click(views[0])
    expect(onChange).toHaveBeenCalledWith(2, null)
  })

  it("shows the failing checks, value included, of the selected scan", async () => {
    renderExplorer(2, null)

    const detail = await screen.findByRole("region", { name: "Scan details" })
    expect(detail).toHaveTextContent("score 66")
    expect(detail).toHaveTextContent("1 failed, 0 errored, 0 waived")
    expect(detail).toHaveTextContent("MFA required")
    expect(detail).toHaveTextContent("2 members without MFA")
    expect(detail).not.toHaveTextContent("Branch protection")
  })

  it("compares two scans oldest to newest whatever order they were picked in", async () => {
    renderExplorer(1, 2)

    const diff = await screen.findByRole("region", { name: "Scan comparison" })
    expect(diff).toHaveTextContent("Newly failing (1)")
    expect(diff).toHaveTextContent("MFA required")
    expect(diff).toHaveTextContent("Fixed (1)")
    expect(diff).toHaveTextContent("Branch protection")
  })

  it("offers the other scans to compare with and reports the pick", async () => {
    const onChange = renderExplorer(2, null)
    await screen.findAllByRole("button", { name: /View scan from/ })

    fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "1" } })

    expect(onChange).toHaveBeenCalledWith(2, 1)
  })

  it("says so when a deep-linked scan is outside the loaded history", async () => {
    renderExplorer(99, null)

    expect(await screen.findByText(/That scan is not in the last 180 days/)).toBeInTheDocument()
  })

  it("shows a loading state, then an error with Retry when the history fails", async () => {
    exportMock.mockRejectedValue(new Error("boom"))
    renderExplorer(null, null)

    expect(screen.getByText("Loading…")).toBeInTheDocument()
    expect(await screen.findByText(/boom/)).toBeInTheDocument()
  })

  it("says so when there are no scans, and when the history is capped", async () => {
    exportMock.mockResolvedValue({ truncated: false, row_count: 0, entries: [] })
    renderExplorer(null, null)
    expect(await screen.findByText(/No scans in the last 180 days/)).toBeInTheDocument()
    cleanup()

    exportMock.mockResolvedValue({ truncated: true, row_count: 2, entries: [NEWER, OLDER] })
    renderExplorer(null, null)
    expect(await screen.findByText(/capped slice/)).toBeInTheDocument()
  })

  it("lists errored and waived checks, tolerating a scan stored without checks", async () => {
    const odd = scan(3, "2026-11-04T10:00:00Z", 50, [
      { id: "organization_members_mfa_required", title: "A", severity: "low", status: "error", value: false, waived: { reason: "x" } },
      { title: "No id", status: "fail", value: null },
    ])
    const bare = { ...scan(4, "2026-11-05T10:00:00Z", 40, []), checks: undefined, score: null }
    exportMock.mockResolvedValue({ truncated: false, row_count: 2, entries: [bare, odd] })
    renderExplorer(3, null)

    const detail = await screen.findByRole("region", { name: "Scan details" })
    expect(detail).toHaveTextContent("1 failed, 1 errored, 1 waived")
    expect(detail).toHaveTextContent("waived")
    expect(detail).toHaveTextContent("Disabled")
    expect(detail).toHaveTextContent("No id")
    cleanup()

    renderExplorer(4, null)
    expect(await screen.findByRole("region", { name: "Scan details" })).toHaveTextContent("score n/a")
    expect(screen.getByText("No failing or errored checks in this scan.")).toBeInTheDocument()
  })

  it("shows checks whose value changed between two scans, and empty sections", async () => {
    const a = scan(5, "2026-11-01T10:00:00Z", 60, [
      { id: "bp", title: "Branch protection", status: "fail", value: { checked: 5, protected: 1 } },
    ])
    const b = scan(6, "2026-11-02T10:00:00Z", 55, [
      { id: "bp", title: "Branch protection", status: "fail", value: { checked: 5, protected: 2 } },
    ])
    const c = { ...scan(7, "2026-11-03T10:00:00Z", null as unknown as number, []), score: null }
    exportMock.mockResolvedValue({ truncated: false, row_count: 3, entries: [c, b, a] })
    renderExplorer(6, 5)

    const diff = await screen.findByRole("region", { name: "Scan comparison" })
    expect(diff).toHaveTextContent("Changed value (1)")
    expect(diff).toHaveTextContent("1/5 → 2/5")
    expect(diff).toHaveTextContent("Nothing newly failing.")
    expect(diff).toHaveTextContent("Nothing fixed.")
    cleanup()

    renderExplorer(7, 6)
    const none = await screen.findByRole("region", { name: "Scan comparison" })
    expect(none).toHaveTextContent("No failing check changed value.")
    expect(none).toHaveTextContent("score n/a")
  })

  it("disables the compare picker until a scan is open", async () => {
    renderExplorer(null, null)
    await screen.findAllByRole("button", { name: /View scan from/ })
    expect(screen.getByLabelText("Compare with")).toBeDisabled()
  })
})
