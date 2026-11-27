import { describe, expect, it } from "vitest"
import { CONFIG_FIELDS, CONFIG_GROUPS, CONFIG_LIMITS, limitsText, validateConfigValue } from "@/lib/config-fields"

const field = (key: string) => {
  const f = CONFIG_FIELDS.find((x) => x.key === key)
  if (!f) throw new Error(key)
  return f
}

describe("config field table", () => {
  it("puts every field in exactly one group, and lists only real fields", () => {
    const grouped = CONFIG_GROUPS.flatMap((g) => g.keys)
    expect(new Set(grouped).size).toBe(grouped.length)
    expect([...grouped].sort()).toEqual(CONFIG_FIELDS.map((f) => f.key).sort())
  })

  it("has a range for every number field and for nothing else", () => {
    const numeric = CONFIG_FIELDS.filter((f) => f.type === "number").map((f) => f.key).sort()
    expect(Object.keys(CONFIG_LIMITS).sort()).toEqual(numeric)
    for (const l of Object.values(CONFIG_LIMITS)) expect(l.min).toBeLessThanOrEqual(l.max)
  })

  it("describes the retention minimums the sweep enforces", () => {
    expect(CONFIG_LIMITS.retention_webhook_deliveries_days.min).toBe(30)
    expect(CONFIG_LIMITS.retention_jobs_days.min).toBe(8)
    expect(CONFIG_LIMITS.retention_repo_events_days.min).toBe(100)
    // The row descriptions state the same minimums.
    expect(field("retention_jobs_days").description).toMatch(/minimum 8/)
  })
})

describe("validateConfigValue", () => {
  it("accepts values inside the range, with whitespace around them", () => {
    expect(validateConfigValue(field("worker_poll_seconds"), "1")).toBeNull()
    expect(validateConfigValue(field("worker_poll_seconds"), " 30 ")).toBeNull()
  })

  it("rejects anything outside it, and anything that is not a whole number", () => {
    expect(validateConfigValue(field("worker_poll_seconds"), "0")).toMatch(/Use 1 to 30/)
    expect(validateConfigValue(field("worker_poll_seconds"), "31")).toMatch(/Use 1 to 30/)
    for (const bad of ["", " ", "abc", "1.5", "1e2", "-3"]) {
      expect(validateConfigValue(field("worker_poll_seconds"), bad)).not.toBeNull()
    }
  })

  it("lets a retention window be 0, but not between 1 and the minimum", () => {
    expect(validateConfigValue(field("retention_jobs_days"), "0")).toBeNull()
    expect(validateConfigValue(field("retention_jobs_days"), "7")).toMatch(/0 \(keep forever\) or 8 to 3650/)
    expect(validateConfigValue(field("retention_jobs_days"), "8")).toBeNull()
    expect(validateConfigValue(field("retention_jobs_days"), "3651")).not.toBeNull()
  })

  it("has no rule for select and boolean fields", () => {
    expect(validateConfigValue(field("digest_cadence"), "whatever")).toBeNull()
    expect(validateConfigValue(field("registration_enabled"), "")).toBeNull()
  })

  it("words the range", () => {
    expect(limitsText("gap_heal_poll_seconds")).toBe("60 to 3600")
    expect(limitsText("retention_jobs_days")).toBe("0 (keep forever) or 8 to 3650")
    expect(limitsText("digest_cadence")).toBeNull()
  })
})
