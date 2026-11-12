import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const exportMock = vi.fn()
vi.mock("@/lib/api/client", () => ({
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
  { id: "mfa", title: "MFA required", severity: "high", status: "fail", value: { type: "text", text: "2 members without MFA" } },
  { id: "bp", title: "Branch protection", severity: "high", status: "pass", value: null },
])
const OLDER = scan(1, "2026-10-20T10:00:00Z", 83, [
  { id: "mfa", title: "MFA required", severity: "high", status: "pass", value: null },
  { id: "bp", title: "Branch protection", severity: "high", status: "fail", value: { type: "ratio", numerator: 3, denominator: 5 } },
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
})
