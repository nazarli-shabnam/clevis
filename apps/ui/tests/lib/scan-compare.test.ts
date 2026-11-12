import { describe, expect, it } from "vitest"

import { checkValueSummary, diffScans, scanCounts, type StoredCheck } from "@/lib/scan-compare"

const check = (id: string, status: StoredCheck["status"], value: StoredCheck["value"] = null, extra: StoredCheck = {}): StoredCheck => ({
  id,
  title: id.toUpperCase(),
  severity: "high",
  status,
  value,
  ...extra,
})

describe("checkValueSummary", () => {
  it("summarises each value shape and tolerates none", () => {
    expect(checkValueSummary(null)).toBe("")
    expect(checkValueSummary({ type: "text", text: "rate limited" })).toBe("rate limited")
    expect(checkValueSummary({ type: "boolean", enabled: true })).toBe("Enabled")
    expect(checkValueSummary({ type: "boolean", enabled: false })).toBe("Disabled")
    expect(checkValueSummary({ type: "mystery" } as never)).toBe("")
    expect(checkValueSummary({ type: "ratio", numerator: 3, denominator: 5 })).toBe("3/5")
    expect(checkValueSummary({ type: "severity_counts", critical: 2, high: 1, medium: 0, low: 0 })).toBe("2 critical, 1 high")
    expect(checkValueSummary({ type: "severity_counts", critical: 0, high: 0, medium: 0, low: 0 })).toBe("No open alerts")
  })
})

describe("scanCounts", () => {
  it("counts failed, errored and waived checks", () => {
    const checks = [check("a", "fail"), check("b", "error"), check("c", "pass"), check("d", "fail", null, { waived: { reason: "x" } as never })]
    expect(scanCounts(checks)).toEqual({ failed: 2, errored: 1, waived: 1 })
  })
})

describe("diffScans", () => {
  it("separates newly failing, fixed and changed-value checks", () => {
    const older = [
      check("mfa", "pass"),
      check("bp", "fail", { type: "ratio", numerator: 3, denominator: 5 }),
      check("secrets", "fail", { type: "ratio", numerator: 1, denominator: 5 }),
      check("same", "fail", { type: "text", text: "x" }),
    ]
    const newer = [
      check("mfa", "fail"),
      check("bp", "pass"),
      check("secrets", "fail", { type: "ratio", numerator: 2, denominator: 5 }),
      check("same", "fail", { type: "text", text: "x" }),
      check("new-check", "error"),
    ]

    const diff = diffScans(older, newer)

    expect(diff.newlyFailing.map((c) => c.id)).toEqual(["mfa", "new-check"])
    expect(diff.fixed.map((c) => c.id)).toEqual(["bp"])
    expect(diff.changed).toEqual([{ check: newer[2], before: "1/5", after: "2/5" }])
  })

  it("reports nothing when two scans match", () => {
    const scan = [check("a", "fail"), check("b", "pass")]
    expect(diffScans(scan, scan)).toEqual({ newlyFailing: [], fixed: [], changed: [] })
  })
})
