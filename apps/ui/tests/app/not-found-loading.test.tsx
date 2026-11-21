import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import Loading from "@/app/loading"
import NotFound from "@/app/not-found"

describe("not-found and loading pages", () => {
  it("not-found links back to the Overview", () => {
    render(<NotFound />)
    expect(screen.getByText("Page not found")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /back to overview/i })).toHaveAttribute("href", "/")
  })

  it("loading renders an accessible skeleton", () => {
    render(<Loading />)
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument()
  })
})
