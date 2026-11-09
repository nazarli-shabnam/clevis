import { describe, expect, it } from "vitest"
import { EMPTY_FORM, toAuditFilters } from "@/lib/audit-filters"

describe("toAuditFilters", () => {
  it("returns no filters for an empty form", () => {
    expect(toAuditFilters(EMPTY_FORM)).toEqual({})
  })

  it("trims text fields and drops blank ones", () => {
    expect(toAuditFilters({ ...EMPTY_FORM, actionPrefix: " token. ", actor: "  ", target: "acme" })).toEqual({
      action_prefix: "token.",
      target: "acme",
    })
  })

  it("turns whole UTC days into since and an exclusive until the day after `to`", () => {
    expect(toAuditFilters({ ...EMPTY_FORM, from: "2026-03-05", to: "2026-03-31" })).toEqual({
      since: "2026-03-05T00:00:00Z",
      until: "2026-04-01T00:00:00.000Z",
    })
  })
})
