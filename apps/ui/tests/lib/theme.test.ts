import { describe, expect, it } from "vitest"
import { normalizeTheme } from "@/lib/theme"

describe("normalizeTheme", () => {
  it("keeps current preferences", () => {
    expect(normalizeTheme("dark")).toBe("dark")
    expect(normalizeTheme("light")).toBe("light")
    expect(normalizeTheme("system")).toBe("system")
  })

  it("maps the retired light themes to light", () => {
    expect(normalizeTheme("paper")).toBe("light")
    expect(normalizeTheme("ash")).toBe("light")
  })

  it("maps the retired dark themes and unknown values to dark", () => {
    for (const v of ["midnight", "carbon", "slate", "dim", "nope", "", null]) {
      expect(normalizeTheme(v)).toBe("dark")
    }
  })
})
