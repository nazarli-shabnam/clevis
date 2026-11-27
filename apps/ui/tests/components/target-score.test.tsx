import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getMock = vi.fn()
const setMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: {
      targetScore: (...a: unknown[]) => getMock(...a),
      setTargetScore: (...a: unknown[]) => setMock(...a),
    },
  },
}))

import { TargetScoreBanner } from "@/components/target-score-banner"
import { TargetScoreCard } from "@/components/target-score-card"

function wrap(node: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

beforeEach(() => {
  getMock.mockReset()
  setMock.mockReset()
})
afterEach(cleanup)

describe("TargetScoreCard", () => {
  it("shows the saved target and saves a new one", async () => {
    getMock.mockResolvedValue({ target: 80 })
    setMock.mockResolvedValue({ target: 85 })
    wrap(<TargetScoreCard orgLogin="acme" />)

    const input = await screen.findByLabelText(/Keep the security score at or above/)
    expect(input).toHaveValue("80")
    const save = screen.getByRole("button", { name: "Save" })
    expect(save).toBeDisabled()

    fireEvent.change(input, { target: { value: " 85 " } })
    fireEvent.click(save)

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", 85))
    expect(await screen.findByText("Saved.")).toBeInTheDocument()
  })

  it("clears the target when the field is emptied", async () => {
    getMock.mockResolvedValue({ target: 80 })
    setMock.mockResolvedValue({ target: null })
    wrap(<TargetScoreCard orgLogin="acme" />)

    fireEvent.change(await screen.findByLabelText(/Keep the security score at or above/), { target: { value: "" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", null))
  })

  it("refuses a value that is not a whole number from 1 to 100", async () => {
    getMock.mockResolvedValue({ target: null })
    wrap(<TargetScoreCard orgLogin="acme" />)

    const input = await screen.findByLabelText(/Keep the security score at or above/)
    for (const bad of ["0", "101", "8.5", "abc"]) {
      fireEvent.change(input, { target: { value: bad } })
      expect(screen.getByText(/whole number from 1 to 100/)).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    }
    expect(setMock).not.toHaveBeenCalled()
  })

  it("shows the server's error and a retryable load error", async () => {
    getMock.mockResolvedValueOnce({ target: null })
    setMock.mockRejectedValue(new Error("Only admins can do that"))
    wrap(<TargetScoreCard orgLogin="acme" />)

    fireEvent.change(await screen.findByLabelText(/Keep the security score at or above/), { target: { value: "90" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    expect(await screen.findByText("Only admins can do that")).toBeInTheDocument()
    cleanup()

    getMock.mockRejectedValue(new Error("boom"))
    wrap(<TargetScoreCard orgLogin="acme" />)
    expect(await screen.findByText(/Couldn't load the target score: boom/)).toBeInTheDocument()
  })
})

describe("TargetScoreBanner", () => {
  it("says how many points the score is under the target", () => {
    render(<TargetScoreBanner score={72} target={85} />)
    expect(screen.getByRole("status")).toHaveTextContent("Score 72 is 13 points below the organization’s target of 85.")
    cleanup()
    render(<TargetScoreBanner score={84} target={85} />)
    expect(screen.getByRole("status")).toHaveTextContent("1 point below")
  })

  it("shows nothing without a target or score, or once the target is met", () => {
    for (const [score, target] of [[72, undefined], [null, 85], [85, 85], [99, 85]] as const) {
      const { container } = render(<TargetScoreBanner score={score} target={target} />)
      expect(container).toBeEmptyDOMElement()
      cleanup()
    }
  })
})
