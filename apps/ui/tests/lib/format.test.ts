import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import {
  relativeTime,
  exactTime,
  formatBytes,
  jobTypeLabel,
  classifyStaleness,
  stalenessColor,
} from "@/lib/format"

const NOW = new Date("2026-05-26T12:00:00Z").getTime()

beforeEach(() => {
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe("relativeTime", () => {
  it("returns 'just now' for < 60s", () => {
    const iso = new Date(NOW - 30_000).toISOString()
    expect(relativeTime(iso)).toBe("just now")
  })

  it("returns 'just now' for future timestamps", () => {
    const iso = new Date(NOW + 5_000).toISOString()
    expect(relativeTime(iso)).toBe("just now")
  })

  it("returns minutes", () => {
    const iso = new Date(NOW - 3 * 60_000).toISOString()
    expect(relativeTime(iso)).toBe("3 minutes ago")
  })

  it("singular minute", () => {
    const iso = new Date(NOW - 60_000).toISOString()
    expect(relativeTime(iso)).toBe("1 minute ago")
  })

  it("returns hours", () => {
    const iso = new Date(NOW - 5 * 3600_000).toISOString()
    expect(relativeTime(iso)).toBe("5 hours ago")
  })

  it("returns days", () => {
    const iso = new Date(NOW - 3 * 86400_000).toISOString()
    expect(relativeTime(iso)).toBe("3 days ago")
  })

  it("returns months", () => {
    const iso = new Date(NOW - 40 * 86400_000).toISOString()
    expect(relativeTime(iso)).toBe("1 month ago")
  })

  it("returns years", () => {
    const iso = new Date(NOW - 400 * 86400_000).toISOString()
    expect(relativeTime(iso)).toBe("1 year ago")
  })

  it("handles invalid ISO gracefully", () => {
    expect(relativeTime("not-a-date")).toBe("unknown")
  })
})

describe("formatBytes", () => {
  it("0 bytes", () => expect(formatBytes(0)).toBe("0 B"))
  it("bytes",   () => expect(formatBytes(512)).toBe("512 B"))
  it("KB",      () => expect(formatBytes(1536)).toBe("1.5 KB"))
  it("MB",      () => expect(formatBytes(2.5 * 1024 * 1024)).toBe("2.5 MB"))
  it("GB",      () => expect(formatBytes(1.1 * 1024 ** 3)).toBe("1.1 GB"))
})

describe("jobTypeLabel", () => {
  it("maps known slug", () => {
    expect(jobTypeLabel("github.clear_actions_cache")).toBe("Clear Actions Cache")
  })

  it("title-cases unknown slug", () => {
    expect(jobTypeLabel("foo.do_something_cool")).toBe("Do Something Cool")
  })

  it("handles no dots", () => {
    expect(jobTypeLabel("mytask")).toBe("Mytask")
  })
})

describe("classifyStaleness", () => {
  it("fresh < 7 days", () => {
    const iso = new Date(NOW - 3 * 86400_000).toISOString()
    expect(classifyStaleness(iso)).toBe("fresh")
  })

  it("stale 7–30 days", () => {
    const iso = new Date(NOW - 15 * 86400_000).toISOString()
    expect(classifyStaleness(iso)).toBe("stale")
  })

  it("old > 30 days", () => {
    const iso = new Date(NOW - 60 * 86400_000).toISOString()
    expect(classifyStaleness(iso)).toBe("old")
  })
})

describe("stalenessColor", () => {
  it("has all three keys with text + dot", () => {
    for (const key of ["fresh", "stale", "old"] as const) {
      expect(stalenessColor[key].text).toBeTruthy()
      expect(stalenessColor[key].dot).toBeTruthy()
    }
  })
})
