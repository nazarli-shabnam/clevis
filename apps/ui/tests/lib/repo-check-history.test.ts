import { describe, expect, it } from "vitest"
import { repoCheckHistory, repoCheckState, type StoredCheck } from "@/lib/scan-compare"

const ratio = (status: StoredCheck["status"], repos?: string[]): StoredCheck => ({
  id: "bp",
  title: "Branch protection",
  status,
  value: { type: "ratio", numerator: 1, denominator: 3, repos },
})
const names = (n: number) => Array.from({ length: n }, (_, i) => `r${i}`)

describe("repoCheckState", () => {
  it("is fail when the failing check names the repo, ignoring case", () => {
    expect(repoCheckState(ratio("fail", ["Legacy"]), "legacy")).toBe("fail")
  })

  it("is pass when the check passed, or failed on other repos in a complete list", () => {
    expect(repoCheckState(ratio("pass"), "legacy")).toBe("pass")
    expect(repoCheckState(ratio("fail", ["other"]), "legacy")).toBe("pass")
  })

  it("is unknown for a repo the scan could not read, even though it names other failing repos", () => {
    const unreadable: StoredCheck = { id: "bp", status: "fail", value: { type: "ratio", numerator: 8, denominator: 10, unknown: 1, repos: ["a"] } }
    expect(repoCheckState(unreadable, "b")).toBe("unknown")
    expect(repoCheckState(unreadable, "a")).toBe("fail")
    expect(repoCheckState({ ...unreadable, status: "pass" }, "b")).toBe("unknown")
  })

  it("reads severity-count checks too", () => {
    const alerts = (repos: string[]): StoredCheck => ({ id: "d", status: "fail", value: { type: "severity_counts", critical: 1, high: 0, medium: 0, low: 0, repos } })
    expect(repoCheckState(alerts(["legacy"]), "legacy")).toBe("fail")
    expect(repoCheckState(alerts(["other"]), "legacy")).toBe("pass")
  })

  it("is unknown when the scan cannot say", () => {
    expect(repoCheckState(ratio("fail"), "legacy")).toBe("unknown") // no names recorded
    expect(repoCheckState(ratio("fail", names(25)), "legacy")).toBe("unknown") // list hit the storage cap
    expect(repoCheckState(ratio("error"), "legacy")).toBe("unknown")
    expect(repoCheckState(ratio("not_applicable"), "legacy")).toBe("unknown")
    expect(repoCheckState({ id: "x", value: { type: "ratio", numerator: 0, denominator: 0 } }, "legacy")).toBe("unknown")
  })

  it("is null for checks that are not per repository", () => {
    expect(repoCheckState({ id: "mfa", status: "fail", value: { type: "boolean", enabled: false } }, "legacy")).toBeNull()
    expect(repoCheckState({ id: "x", status: "pass" }, "legacy")).toBeNull()
  })
})

describe("repoCheckHistory", () => {
  const scan = (id: number, day: number, check: StoredCheck) => ({ id, created_at: `2026-11-0${day}T00:00:00Z`, checks: [check] })

  it("finds when the current run began, whatever order the scans arrive in", () => {
    const [h] = repoCheckHistory(
      [scan(3, 3, ratio("fail", ["legacy"])), scan(1, 1, ratio("pass")), scan(2, 2, ratio("fail", ["legacy"]))],
      "legacy",
    )
    expect(h.points.map((p) => p.state)).toEqual(["pass", "fail", "fail"])
    expect(h).toMatchObject({ current: "fail", since: "2026-11-02T00:00:00Z", sinceAtLeast: false })
  })

  it("marks a run that reaches the oldest scan as 'at least'", () => {
    const [h] = repoCheckHistory([scan(1, 1, ratio("fail", ["legacy"])), scan(2, 2, ratio("fail", ["legacy"]))], "legacy")
    expect(h.sinceAtLeast).toBe(true)
  })

  it("keeps a fixed check, saying when it recovered, and lists failing ones first", () => {
    const other = (s: StoredCheck["status"], repos?: string[]) => ({ ...ratio(s, repos), id: "aa", title: "A check" })
    const out = repoCheckHistory(
      [
        { id: 1, created_at: "2026-11-01T00:00:00Z", checks: [ratio("fail", ["legacy"]), other("fail", ["legacy"])] },
        { id: 2, created_at: "2026-11-02T00:00:00Z", checks: [ratio("pass"), other("fail", ["legacy"])] },
      ],
      "legacy",
    )
    expect(out.map((h) => [h.id, h.current])).toEqual([["aa", "fail"], ["bp", "pass"]])
    expect(out[1].since).toBe("2026-11-02T00:00:00Z")
  })

  it("leaves out checks the repo never failed and checks without an id", () => {
    expect(repoCheckHistory([scan(1, 1, ratio("pass")), scan(2, 2, { ...ratio("fail", ["legacy"]), id: undefined })], "legacy")).toEqual([])
  })
})
