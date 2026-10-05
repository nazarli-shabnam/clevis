import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getMock = vi.fn()
const setMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: {
      hygieneScoring: (...a: unknown[]) => getMock(...a),
      setHygieneScoring: (...a: unknown[]) => setMock(...a),
    },
  },
}))

import { HygieneScoringCard } from "@/components/hygiene-scoring-card"

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <HygieneScoringCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  getMock.mockReset()
  setMock.mockReset()
})
afterEach(cleanup)

describe("HygieneScoringCard", () => {
  it("shows the instance default in the follow option and the effective state", async () => {
    getMock.mockResolvedValue({ enabled: null, effective: false, instance_default: false })
    renderCard()

    expect(await screen.findByRole("option", { name: "Follow the instance setting (off)" })).toBeInTheDocument()
    expect(screen.getByLabelText(/Count repo hygiene checks/)).toHaveValue("follow")
    expect(screen.getByText(/left out of/)).toBeInTheDocument()
  })

  it("saves 'always count' as true and 'follow' as null", async () => {
    getMock.mockResolvedValue({ enabled: null, effective: false, instance_default: false })
    setMock.mockResolvedValue({ enabled: true, effective: true, instance_default: false })
    renderCard()

    const select = await screen.findByLabelText(/Count repo hygiene checks/)
    fireEvent.change(select, { target: { value: "on" } })

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", true))
    await waitFor(() => expect(screen.getByText(/counted in/)).toBeInTheDocument())

    setMock.mockResolvedValue({ enabled: null, effective: false, instance_default: false })
    fireEvent.change(screen.getByLabelText(/Count repo hygiene checks/), { target: { value: "follow" } })
    await waitFor(() => expect(setMock).toHaveBeenLastCalledWith("acme", null))
  })

  it("shows a save error", async () => {
    getMock.mockResolvedValue({ enabled: false, effective: false, instance_default: true })
    setMock.mockRejectedValue(new Error("Forbidden"))
    renderCard()

    fireEvent.change(await screen.findByLabelText(/Count repo hygiene checks/), { target: { value: "on" } })

    expect(await screen.findByText("Forbidden")).toBeInTheDocument()
  })

  it("shows a load error with retry instead of the control", async () => {
    getMock.mockRejectedValue(new Error("boom"))
    renderCard()

    expect(await screen.findByText(/Couldn't load the score settings: boom/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Count repo hygiene checks/)).not.toBeInTheDocument()
    getMock.mockResolvedValue({ enabled: null, effective: false, instance_default: false })
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    expect(await screen.findByLabelText(/Count repo hygiene checks/)).toBeInTheDocument()
  })
})
