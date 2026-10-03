import { describe, expect, it } from "vitest"
import { isPublicRoute } from "@/lib/public-routes"

describe("isPublicRoute", () => {
  it.each(["/login", "/setup", "/register", "/verify-email", "/invite/abc123"])("treats %s as public", (p) => {
    expect(isPublicRoute(p)).toBe(true)
  })

  it.each(["/", "/repos", "/settings", "/invite", "/login/extra"])("treats %s as protected", (p) => {
    expect(isPublicRoute(p)).toBe(false)
  })
})
