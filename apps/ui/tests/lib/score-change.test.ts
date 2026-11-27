import { describe, expect, it } from "vitest"
import { scoreChangeFrom, scoreChangeText } from "@/lib/score-change"
import type { ScanChanges } from "@/lib/api/types"

const changes = (over: Partial<ScanChanges> = {}): ScanChanges => ({
  org: "acme",
  has_previous: true,
  previous_scanned_at: "2026-11-26T00:00:00Z",
  scanned_at: "2026-11-27T00:00:00Z",
  previous_score: 80,
  score: 85,
  comparable: true,
  changes: [],
  ...over,
})

describe("scoreChangeFrom", () => {
  it("is the points between the two latest scans", () => {
    expect(scoreChangeFrom(changes(), 85)).toEqual({ points: 5 })
    expect(scoreChangeFrom(changes({ previous_score: 90 }), 85)).toEqual({ points: -5 })
    expect(scoreChangeFrom(changes({ previous_score: 85 }), 85)).toEqual({ points: 0 })
  })

  it("does not say a number when the scores are not on the same basis", () => {
    expect(scoreChangeFrom(changes({ comparable: false }), 85)).toEqual({ note: "Not comparable with the previous scan" })
  })

  it("is nothing without a previous scan, a score on either side, or data", () => {
    expect(scoreChangeFrom(undefined, 85)).toBeNull()
    expect(scoreChangeFrom(changes({ has_previous: false }), 85)).toBeNull()
    expect(scoreChangeFrom(changes({ previous_score: null }), 85)).toBeNull()
    expect(scoreChangeFrom(changes({ score: null }), null)).toBeNull()
  })

  it("is nothing when the card shows a different scan than the comparison ends at", () => {
    expect(scoreChangeFrom(changes(), 70)).toBeNull()
    // No card score to check against (it is still loading): the comparison stands on its own.
    expect(scoreChangeFrom(changes(), undefined)).toEqual({ points: 5 })
    expect(scoreChangeFrom(changes(), null)).toEqual({ points: 5 })
  })
})

describe("scoreChangeText", () => {
  it("signs the change and says when there is none", () => {
    expect(scoreChangeText({ points: 3 })).toBe("+3 vs last scan")
    expect(scoreChangeText({ points: -5 })).toBe("-5 vs last scan")
    expect(scoreChangeText({ points: 0 })).toBe("No change vs last scan")
  })
})
