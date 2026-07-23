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

  it("accepts hyphens in owner and hyphens/underscores/periods in repo", () => {
    expect(parseOwnerRepo("my-org~my_repo.name")).toEqual({ owner: "my-org", repo: "my_repo.name" });
  });

  it("rejects owner/repo values outside GitHub's allowed charset", () => {
    expect(parseOwnerRepo("acme/evil~demo")).toBeNull();
    expect(parseOwnerRepo("acme~demo/../etc")).toBeNull();
    expect(parseOwnerRepo("-leading-hyphen~demo")).toBeNull();
    expect(parseOwnerRepo("trailing-hyphen-~demo")).toBeNull();
    expect(parseOwnerRepo("double--hyphen~demo")).toBeNull();
    expect(parseOwnerRepo("acme~demo?query=1")).toBeNull();
  });
});
