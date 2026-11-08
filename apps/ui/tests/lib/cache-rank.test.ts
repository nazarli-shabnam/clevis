import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { rankByCacheSize, summarizeCaches } from "@/lib/cache-rank"
import type { CacheEntry } from "@/lib/api/types"

const NOW = new Date("2026-07-31T00:00:00Z")
const daysAgo = (n: number) => new Date(NOW.getTime() - n * 86_400_000).toISOString()

function entry(size: number, accessedDaysAgo: number): CacheEntry {
  return { id: 1, ref: "refs/heads/main", key: "k", version: "v", size_in_bytes: size, created_at: daysAgo(90), last_accessed_at: daysAgo(accessedDaysAgo) }
}

describe("cache ranking", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
  })
  afterEach(() => vi.useRealTimers())

  it("sums size, counts only entries idle 7+ days as stale, and keeps the latest access", () => {
    const stats = summarizeCaches("api", [entry(100, 1), entry(200, 10), entry(400, 60)])
    expect(stats).toEqual({ repo: "api", count: 3, totalBytes: 700, staleBytes: 600, lastAccessed: daysAgo(1) })
  })

  it("summarizes a repo with no entries as empty", () => {
    expect(summarizeCaches("api", [])).toEqual({ repo: "api", count: 0, totalBytes: 0, staleBytes: 0, lastAccessed: null })
  })

  it("ranks biggest first, ties by name, and drops repos with no cache", () => {
    const rows = [
      summarizeCaches("b", [entry(100, 1)]),
      summarizeCaches("a", [entry(100, 1)]),
      summarizeCaches("big", [entry(900, 1)]),
      summarizeCaches("none", []),
    ]
    expect(rankByCacheSize(rows, false).map((r) => r.repo)).toEqual(["big", "a", "b"])
  })

  it("stale-only keeps repos with a stale entry and ranks them by stale bytes, not total", () => {
    const rows = [
      summarizeCaches("fresh-big", [entry(9000, 1)]),
      summarizeCaches("mostly-fresh", [entry(5000, 1), entry(10, 20)]),
      summarizeCaches("stale", [entry(300, 40)]),
    ]
    expect(rankByCacheSize(rows, true).map((r) => r.repo)).toEqual(["stale", "mostly-fresh"])
  })
})
