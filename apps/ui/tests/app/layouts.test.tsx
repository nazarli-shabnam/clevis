import { describe, expect, it } from "vitest"

import ForgotLayout, { metadata as forgot } from "@/app/forgot-password/layout"
import ResetLayout, { metadata as reset } from "@/app/reset-password/layout"

describe("auth page layouts", () => {
  it("only set a page title and render their children", () => {
    expect(forgot.title).toMatch(/forgot/i)
    expect(reset.title).toMatch(/reset/i)
    expect(ForgotLayout({ children: "x" })).toBeTruthy()
    expect(ResetLayout({ children: "x" })).toBeTruthy()
  })
})
