import { describe, expect, it } from "vitest";

import { eventItemHref, repoPageHref } from "@/lib/activity-links";

describe("repoPageHref", () => {
  it("builds the owner~repo route and rejects anything that is not owner/name", () => {
    expect(repoPageHref("acme/api")).toBe("/repos/acme~api");
    expect(repoPageHref("api")).toBeNull();
    expect(repoPageHref("a/b/c")).toBeNull();
  });
});

describe("eventItemHref", () => {
  it("links PRs and issues named in a summary", () => {
    expect(eventItemHref("acme/api", "opened PR #12: Fix it")?.href).toBe("https://github.com/acme/api/pull/12");
    expect(eventItemHref("acme/api", "closed issue #7: Bug")?.href).toBe("https://github.com/acme/api/issues/7");
  });

  it("returns null when the summary names no item", () => {
    expect(eventItemHref("acme/api", "pushed 2 commits to main")).toBeNull();
  });
});
