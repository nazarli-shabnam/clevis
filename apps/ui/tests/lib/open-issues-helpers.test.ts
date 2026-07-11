import { describe, expect, it } from "vitest";

import { initialConfigValues, mergeSavedConfigValue } from "@/lib/config-values";
import { parseOwnerRepo } from "@/lib/repo-segment";
import { shouldApplyResolvedToken } from "@/lib/token-resolve";

describe("config-values", () => {
  it("preserves unsaved edits when config refetches", () => {
    const current = { worker_poll_seconds: "45", registration_enabled: "false" };
    const server = { worker_poll_seconds: "30", registration_enabled: "true" };
    expect(initialConfigValues(current, server)).toEqual(current);
  });
});

describe("repo-segment", () => {
  it("rejects malformed owner~repo segments", () => {
    expect(parseOwnerRepo("only-owner")).toBeNull();
    expect(parseOwnerRepo("owner~")).toBeNull();
  });

  it("parses valid owner~repo segments", () => {
    expect(parseOwnerRepo("acme~demo")).toEqual({ owner: "acme", repo: "demo" });
  });
});

describe("token-resolve", () => {
  it("ignores stale resolve responses for a different owner", () => {
    expect(shouldApplyResolvedToken("acme", "other-org")).toBe(false);
    expect(shouldApplyResolvedToken("acme", "acme")).toBe(true);
  });
});
