import { describe, expect, it } from "vitest";
import { matchScore, rankCommands, type Command } from "@/lib/command-palette";

const cmd = (id: string, kind: Command["kind"], label: string, hint = ""): Command => ({ id, kind, label, hint, href: "/x" });

describe("matchScore", () => {
  it("ranks prefix over word-start over substring over subsequence", () => {
    expect(matchScore("acme/api", "acme")).toBe(3);
    expect(matchScore("acme/api", "api")).toBe(2);
    expect(matchScore("acme/graphql", "raph")).toBe(1);
    expect(matchScore("acme/api", "aai")).toBe(0.5);
    expect(matchScore("acme/api", "zzz")).toBeNull();
  });

  it("is case-insensitive and treats an empty query as a match", () => {
    expect(matchScore("Health & Security", "HEALTH")).toBe(3);
    expect(matchScore("anything", "  ")).toBe(1);
  });
});

describe("rankCommands", () => {
  const all = [
    cmd("p1", "page", "Pull Requests"),
    cmd("r1", "repo", "acme/pulls-tool", "tracks pull requests"),
    cmd("s1", "scope", "Switch to acme"),
    cmd("r2", "repo", "acme/api"),
  ];

  it("returns everything in kind order for an empty query", () => {
    expect(rankCommands(all, "").map((c) => c.id)).toEqual(["p1", "s1", "r1", "r2"]);
  });

  it("drops non-matches and puts the best match first", () => {
    expect(rankCommands(all, "pull").map((c) => c.id)).toEqual(["p1", "r1"]);
    expect(rankCommands(all, "api")[0].id).toBe("r2");
  });

  it("also searches the hint, at lower weight than the label", () => {
    expect(rankCommands([cmd("a", "repo", "x/one", "billing service"), cmd("b", "repo", "billing")], "billing").map((c) => c.id)).toEqual(["b", "a"]);
  });

  it("caps the result count", () => {
    const many = Array.from({ length: 100 }, (_, i) => cmd(`c${i}`, "repo", `acme/r${i}`));
    expect(rankCommands(many, "acme", 10)).toHaveLength(10);
  });
});
