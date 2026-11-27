import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getMock = vi.fn()
const setMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: {
      scoringExclusions: (...a: unknown[]) => getMock(...a),
      setScoringExclusions: (...a: unknown[]) => setMock(...a),
    },
  },
}))

import { ScoringExclusionsCard } from "@/components/scoring-exclusions-card"
import { ScoringExclusionsNote } from "@/components/scoring-exclusions-note"

function wrap(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

beforeEach(() => {
  getMock.mockReset()
  setMock.mockReset()
})
afterEach(cleanup)

describe("ScoringExclusionsCard", () => {
  it("shows the saved patterns one per line and saves a cleaned-up list", async () => {
    getMock.mockResolvedValue({ patterns: ["sandbox-*"] })
    setMock.mockResolvedValue({ patterns: ["sandbox-*", "legacy", "tmp?"] })
    wrap(<ScoringExclusionsCard orgLogin="acme" />)

    const box = await screen.findByLabelText(/Repository names or patterns/)
    expect(box).toHaveValue("sandbox-*")
    const save = screen.getByRole("button", { name: "Save" })
    expect(save).toBeDisabled()

    fireEvent.change(box, { target: { value: "sandbox-*, legacy\n\n tmp?  " } })
    fireEvent.click(save)

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", ["sandbox-*", "legacy", "tmp?"]))
    expect(await screen.findByText("Saved.")).toBeInTheDocument()
    expect(box).toHaveValue("sandbox-*\nlegacy\ntmp?")
  })

  it("clears the exclusions by saving an empty list", async () => {
    getMock.mockResolvedValue({ patterns: ["legacy"] })
    setMock.mockResolvedValue({ patterns: [] })
    wrap(<ScoringExclusionsCard orgLogin="acme" />)

    fireEvent.change(await screen.findByLabelText(/Repository names or patterns/), { target: { value: "" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", []))
  })

  it("shows the server's reason when a pattern is rejected", async () => {
    getMock.mockResolvedValue({ patterns: [] })
    setMock.mockRejectedValue(new Error("'owner/repo' is not a repository name or pattern"))
    wrap(<ScoringExclusionsCard orgLogin="acme" />)

    fireEvent.change(await screen.findByLabelText(/Repository names or patterns/), { target: { value: "owner/repo" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    expect(await screen.findByText(/is not a repository name or pattern/)).toBeInTheDocument()
  })

  it("shows a retryable error when the setting cannot be loaded", async () => {
    getMock.mockRejectedValue(new Error("boom"))
    wrap(<ScoringExclusionsCard orgLogin="acme" />)

    expect(await screen.findByText(/Couldn't load the exclusions: boom/)).toBeInTheDocument()
  })
})

describe("ScoringExclusionsNote", () => {
  it("lists the patterns and the repositories the scan actually skipped", async () => {
    getMock.mockResolvedValue({ patterns: ["sandbox-*"] })
    wrap(<ScoringExclusionsNote orgLogin="acme" matched={["sandbox-1", "sandbox-2"]} />)

    expect(await screen.findByRole("note")).toHaveTextContent(/sandbox-\*.*this scan skipped 2: sandbox-1, sandbox-2/)
    expect(screen.getByRole("link", { name: "Change in settings" })).toHaveAttribute("href", "/settings/org/acme/settings#scanning")
  })

  it("truncates a long list of skipped repositories", async () => {
    getMock.mockResolvedValue({ patterns: ["r*"] })
    wrap(<ScoringExclusionsNote orgLogin="acme" matched={Array.from({ length: 12 }, (_, i) => `r${i}`)} />)

    expect(await screen.findByRole("note")).toHaveTextContent(/skipped 12: r0.*r9, …/)
  })

  it("says nothing without exclusions, or when they cannot be read", async () => {
    getMock.mockResolvedValue({ patterns: [] })
    const { container } = wrap(<ScoringExclusionsNote orgLogin="acme" />)
    await waitFor(() => expect(getMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    cleanup()

    getMock.mockRejectedValue(new Error("403"))
    const second = wrap(<ScoringExclusionsNote orgLogin="other" />)
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2))
    expect(second.container).toBeEmptyDOMElement()
  })
})
