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
    expect(parseOwnerRepo("acme~demo?query=1")).toBeNull();
  });

  it("accepts Enterprise Managed User owners (underscore) and owners with a double hyphen (#1090)", () => {
    expect(parseOwnerRepo("octocat_acme~demo")).toEqual({ owner: "octocat_acme", repo: "demo" });
    expect(parseOwnerRepo("double--hyphen~demo")).toEqual({ owner: "double--hyphen", repo: "demo" });
    // The real safety checks still hold.
    expect(parseOwnerRepo("octo.cat~demo")).toBeNull();
    expect(parseOwnerRepo("..~demo")).toBeNull();
    expect(parseOwnerRepo("a_b/c~demo")).toBeNull();
    expect(parseOwnerRepo("a_b?x=1~demo")).toBeNull();
  });

  it("rejects \".\" and \"..\" as repo names but keeps dotted names", () => {
    expect(parseOwnerRepo("acme~.")).toBeNull();
    expect(parseOwnerRepo("acme~..")).toBeNull();
    expect(parseOwnerRepo("acme~...")).toEqual({ owner: "acme", repo: "..." });
    expect(parseOwnerRepo("acme~.github")).toEqual({ owner: "acme", repo: ".github" });
  });
});
