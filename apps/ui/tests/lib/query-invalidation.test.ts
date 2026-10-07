import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { invalidateInstallations, invalidateTokens } from "@/lib/query-invalidation";
import { hasOrgLogin } from "@/lib/token-resolve";

describe("query invalidation helpers", () => {
  it("invalidateTokens hits both the token list and every resolved-token lookup", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    invalidateTokens(qc);
    expect(spy.mock.calls.map(([f]) => f?.queryKey)).toEqual([["tokens"], ["tokens.resolve"]]);
  });

  it("invalidateInstallations hits the personal, per-org and permissions families", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    invalidateInstallations(qc);
    expect(spy.mock.calls.map(([f]) => f?.queryKey)).toEqual([
      ["installations"],
      ["installations.org"],
      ["installations.permissions"],
    ]);
  });

  it("marks stale cache entries in all three installation families", async () => {
    const qc = new QueryClient();
    qc.setQueryData(["installations"], []);
    qc.setQueryData(["installations.org", "acme"], []);
    qc.setQueryData(["installations.permissions", "org", "acme"], []);
    await invalidateInstallations(qc);
    for (const key of [["installations"], ["installations.org", "acme"], ["installations.permissions", "org", "acme"]]) {
      expect(qc.getQueryState(key)?.isInvalidated).toBe(true);
    }
  });
});

describe("hasOrgLogin", () => {
  it("accepts 1- and 2-character org logins and rejects blank input", () => {
    expect(hasOrgLogin("a")).toBe(true);
    expect(hasOrgLogin(" hp ")).toBe(true);
    expect(hasOrgLogin("   ")).toBe(false);
    expect(hasOrgLogin("")).toBe(false);
  });
});
