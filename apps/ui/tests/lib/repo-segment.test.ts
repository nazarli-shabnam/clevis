import { describe, expect, it } from "vitest";

import { parseOwnerRepo } from "@/lib/repo-segment";

describe("repo-segment", () => {
  it("rejects malformed owner~repo segments", () => {
    expect(parseOwnerRepo("only-owner")).toBeNull();
    expect(parseOwnerRepo("owner~")).toBeNull();
  });

  it("parses valid owner~repo segments", () => {
    expect(parseOwnerRepo("acme~demo")).toEqual({ owner: "acme", repo: "demo" });
  });
});
