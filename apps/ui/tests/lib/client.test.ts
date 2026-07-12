import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api/client";

const TOKEN_KEY = "clevis:token";

describe("del() 401 handling", () => {
  beforeEach(() => {
    localStorage.setItem(TOKEN_KEY, "stale-token");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("clears the stored token and dispatches clevis:unauthorized on a 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 401 }))),
    );

    const dispatchSpy = vi.spyOn(window, "dispatchEvent");

    await expect(api.tokens.delete("acme")).rejects.toThrow();

    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(dispatchSpy).toHaveBeenCalledWith(expect.objectContaining({ type: "clevis:unauthorized" }));
  });

  it("does not clear the token or dispatch clevis:unauthorized on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    );

    const dispatchSpy = vi.spyOn(window, "dispatchEvent");

    await api.tokens.delete("acme");

    expect(localStorage.getItem(TOKEN_KEY)).toBe("stale-token");
    expect(dispatchSpy).not.toHaveBeenCalledWith(expect.objectContaining({ type: "clevis:unauthorized" }));
  });
});

describe("optional token coercion (GitHub App installation fallback)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function stubOkJson(body: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))),
    );
  }

  it("sends token: undefined for analytics.overview when the token field is empty", async () => {
    stubOkJson({ owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [] });
    await api.analytics.overview("acme", "");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ owner: "acme", token: undefined });
  });

  it("sends token: undefined for cache.list when the token field is empty", async () => {
    stubOkJson({ repository: "acme/demo", total: 0, actions_caches: [] });
    await api.cache.list("acme", "demo", "");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ token: undefined });
  });

  it("sends token: undefined for cache.clear when the token field is empty", async () => {
    stubOkJson({ queued: false, dry_run: true });
    await api.cache.clear("acme", "demo", { token: "", actor: "me", dry_run: true });
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ token: undefined, actor: "me", dry_run: true });
  });
});
