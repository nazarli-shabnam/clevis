import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const getMock = vi.fn()
const setMock = vi.fn()

vi.mock("@/lib/api/client", () => ({
  api: { orgs: { badge: (...a: unknown[]) => getMock(...a), setBadge: (...a: unknown[]) => setMock(...a) } },
}))

import { BadgeSettingsCard } from "@/components/badge-settings-card"
import { badgeHtml, badgeMarkdown, badgeUrl } from "@/lib/badge"

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <BadgeSettingsCard orgLogin="acme" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  getMock.mockReset()
  setMock.mockReset()
})
afterEach(cleanup)

describe("badge URLs", () => {
  it("builds the public score.svg URL and both snippets", () => {
    expect(badgeUrl("acme")).toBe("http://localhost:8080/badges/acme/score.svg")
    expect(badgeMarkdown("acme")).toBe("![Clevis security score](http://localhost:8080/badges/acme/score.svg)")
    expect(badgeHtml("acme")).toContain('<img src="http://localhost:8080/badges/acme/score.svg"')
  })
})

describe("BadgeSettingsCard", () => {
  it("is opted out by default: states what becomes public and shows no preview or snippets", async () => {
    getMock.mockResolvedValue({ enabled: false })
    renderCard()

    expect(await screen.findByRole("checkbox")).not.toBeChecked()
    expect(screen.getByText(/Only the bare score/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "#577" })).toHaveAttribute("href", "https://github.com/nazarli-shabnam/clevis/issues/577")
    expect(screen.queryByAltText(/preview/i)).not.toBeInTheDocument()
    expect(screen.queryByText("Markdown")).not.toBeInTheDocument()
  })

  it("opts in, then shows the live preview and snippets", async () => {
    getMock.mockResolvedValue({ enabled: false })
    setMock.mockResolvedValue({ enabled: true })
    renderCard()

    fireEvent.click(await screen.findByRole("checkbox"))

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", true))
    expect(await screen.findByAltText(/preview/i)).toHaveAttribute("src", badgeUrl("acme"))
    expect(screen.getByText(badgeMarkdown("acme"))).toBeInTheDocument()
    expect(screen.getByText(badgeHtml("acme"))).toBeInTheDocument()
  })

  it("copies a snippet to the clipboard", async () => {
    getMock.mockResolvedValue({ enabled: true })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: "Copy Markdown snippet" }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(badgeMarkdown("acme")))
    expect(await screen.findByText("Copied")).toBeInTheDocument()
  })

  it("says so when copying fails", async () => {
    getMock.mockResolvedValue({ enabled: true })
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } })
    renderCard()

    fireEvent.click(await screen.findByRole("button", { name: "Copy HTML snippet" }))

    expect(await screen.findByText("Copy failed")).toBeInTheDocument()
  })

  it("opts out again and hides the preview", async () => {
    getMock.mockResolvedValue({ enabled: true })
    setMock.mockResolvedValue({ enabled: false })
    renderCard()

    fireEvent.click(await screen.findByRole("checkbox"))

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", false))
    await waitFor(() => expect(screen.queryByAltText(/preview/i)).not.toBeInTheDocument())
  })

  it("shows a save error and a load error with retry", async () => {
    getMock.mockResolvedValueOnce({ enabled: false })
    setMock.mockRejectedValue(new Error("Forbidden"))
    renderCard()
    fireEvent.click(await screen.findByRole("checkbox"))
    expect(await screen.findByText("Forbidden")).toBeInTheDocument()

    cleanup()
    getMock.mockRejectedValue(new Error("boom"))
    renderCard()
    expect(await screen.findByText(/Couldn't load the badge setting: boom/)).toBeInTheDocument()
    getMock.mockResolvedValue({ enabled: false })
    fireEvent.click(screen.getByRole("button", { name: /retry/i }))
    expect(await screen.findByRole("checkbox")).toBeInTheDocument()
  })
})
