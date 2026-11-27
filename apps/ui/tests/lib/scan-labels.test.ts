import { describe, expect, it } from "vitest"
import { scanLabels } from "@/lib/scan-labels"

describe("scanLabels", () => {
  it("labels each scan with its day", () => {
    const labels = scanLabels(3, ["2026-11-25T10:00:00Z", "2026-11-26T10:00:00Z", "2026-11-27T10:00:00Z"])

    expect(new Set(labels).size).toBe(3)
    for (const label of labels) expect(label).toMatch(/[A-Za-z]{3}.* \d{1,2}|\d{1,2}.*[A-Za-z]{3}/)
    expect(labels.join(" ")).not.toMatch(/:/) // no time of day when a day has one scan
  })

  it("adds the time when two scans share a day, so no two points read the same", () => {
    const labels = scanLabels(3, ["2026-11-26T09:00:00Z", "2026-11-26T17:00:00Z", "2026-11-27T10:00:00Z"])

    expect(new Set(labels).size).toBe(3)
    expect(labels[0]).toMatch(/:/)
    expect(labels[1]).toMatch(/:/)
    expect(labels[2]).not.toMatch(/:/)
  })

  it("falls back to Scan N without dates, with dates that do not line up, or with an unreadable one", () => {
    expect(scanLabels(2, undefined)).toEqual(["Scan 1", "Scan 2"])
    expect(scanLabels(2, [])).toEqual(["Scan 1", "Scan 2"])
    expect(scanLabels(2, ["2026-11-26T09:00:00Z"])).toEqual(["Scan 1", "Scan 2"])
    expect(scanLabels(2, ["2026-11-26T09:00:00Z", "not a date"])).toEqual(["Scan 1", "Scan 2"])
    expect(scanLabels(0, undefined)).toEqual([])
  })
})
