import { describe, expect, it } from "vitest"

import { payloadEntries, payloadSummary, prettyPayload } from "@/lib/audit-payload"

describe("payloadEntries", () => {
  it("is empty for an empty object or nothing stored, so the row is not expandable", () => {
    expect(payloadEntries("{}")).toEqual([])
    expect(payloadEntries("")).toEqual([])
  })

  it("lists keys with strings as-is and other values as JSON", () => {
    expect(payloadEntries(JSON.stringify({ check_id: "branch_protection", dry_run: false, repos: ["a", "b"], n: null }))).toEqual([
      ["check_id", "branch_protection"],
      ["dry_run", "false"],
      ["repos", '["a","b"]'],
      ["n", "null"],
    ])
  })

  it("shows non-object JSON and unparseable text whole", () => {
    expect(payloadEntries("42")).toEqual([["payload", "42"]])
    expect(payloadEntries("[1,2]")).toEqual([["payload", "[1,2]"]])
    expect(payloadEntries("not json")).toEqual([["payload", "not json"]])
  })
})

describe("payloadSummary / prettyPayload", () => {
  it("joins pairs on one line", () => {
    expect(payloadSummary('{"a":"x","b":2}')).toBe("a=x, b=2")
    expect(payloadSummary("{}")).toBe("")
  })

  it("pretty-prints JSON and passes other text through", () => {
    expect(prettyPayload('{"a":1}')).toBe('{\n  "a": 1\n}')
    expect(prettyPayload("not json")).toBe("not json")
  })
})
