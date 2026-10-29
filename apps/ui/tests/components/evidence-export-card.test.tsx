import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const downloadMock = vi.fn()
const saveMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { evidence: { download: (...a: unknown[]) => downloadMock(...a) } },
}))
vi.mock("@/lib/download", () => ({ downloadBlob: (...a: unknown[]) => saveMock(...a) }))

import { EvidenceExportCard } from "@/components/evidence-export-card"

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <EvidenceExportCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  downloadMock.mockReset()
  saveMock.mockReset()
})
afterEach(cleanup)

describe("EvidenceExportCard", () => {
  it("downloads the zip for the chosen range and saves it under the server's filename", async () => {
    const blob = new Blob(["zip"])
    downloadMock.mockResolvedValue({ blob, filename: "clevis-evidence-acme-1.zip", truncated: false })
    renderCard()
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-01-01" } })
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-03-31" } })
    fireEvent.click(screen.getByRole("button", { name: "Export evidence" }))

    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith("acme", { since: "2026-01-01", until: "2026-03-31" }))
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith("clevis-evidence-acme-1.zip", blob))
    expect(await screen.findByRole("status")).toHaveTextContent("Downloaded clevis-evidence-acme-1.zip")
  })

  it("shows a preparing state while the zip is built", async () => {
    let finish: (v: unknown) => void = () => {}
    downloadMock.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Export evidence" }))
    expect(await screen.findByRole("button", { name: "Preparing…" })).toBeDisabled()
    finish({ blob: new Blob(["z"]), filename: "e.zip", truncated: false })
    await waitFor(() => expect(saveMock).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByRole("button", { name: "Export evidence" })).toBeEnabled())
  })

  it("warns when a list was capped", async () => {
    const blob = new Blob(["z"])
    downloadMock.mockResolvedValue({ blob, filename: "e.zip", truncated: true })
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Export evidence" }))
    const notice = await screen.findByRole("status")
    expect(notice).toHaveTextContent(/hit their size cap/)
    // The date range only shortens the scan history and audit log, and the notice says so.
    expect(notice).toHaveTextContent(/ignore the range/)
    // A capped export is still saved: the user gets the (incomplete) zip plus the warning.
    await waitFor(() => expect(saveMock).toHaveBeenCalledWith("e.zip", blob))
  })

  it("shows the server's error and saves nothing", async () => {
    downloadMock.mockRejectedValue(new Error("Too many requests"))
    renderCard()
    fireEvent.click(screen.getByRole("button", { name: "Export evidence" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests")
    expect(saveMock).not.toHaveBeenCalled()
  })
})
