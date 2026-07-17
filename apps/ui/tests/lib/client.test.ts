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

  it("omits token for analytics.overview when none is provided (GitHub App path)", async () => {
    stubOkJson({ owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [] });
    await api.analytics.overview("acme");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ owner: "acme" });
  });

  it("omits token for cache.list when none is provided", async () => {
    stubOkJson({ repository: "acme/demo", total: 0, actions_caches: [] });
    await api.cache.list("acme", "demo");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it("omits token for cache.clear when none is provided", async () => {
    stubOkJson({ queued: false, dry_run: true });
    await api.cache.clear("acme", "demo", { actor: "me", dry_run: true });
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ actor: "me", dry_run: true });
  });

  it("forwards an explicit token when callers still pass one", async () => {
    stubOkJson({ owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [] });
    await api.analytics.overview("acme", "ghp_explicit");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ owner: "acme", token: "ghp_explicit" });
  });

  it("forwards an explicit token for cache.list", async () => {
    stubOkJson({ repository: "acme/demo", total: 0, actions_caches: [] });
    await api.cache.list("acme", "demo", "ghp_list");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({ token: "ghp_list" });
  });

  it("forwards token, key, and ref for cache.clear when provided", async () => {
    stubOkJson({ queued: true, dry_run: false, job_id: 9 });
    await api.cache.clear("acme", "demo", {
      actor: "me",
      dry_run: false,
      token: "ghp_clear",
      key: "build-cache",
      ref: "refs/heads/main",
    });
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body as string)).toEqual({
      actor: "me",
      dry_run: false,
      token: "ghp_clear",
      key: "build-cache",
      ref: "refs/heads/main",
    });
  });
});

describe("installations.lookup / installations.sync", () => {
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

  it("GETs /me/installations/lookup/{id}", async () => {
    stubOkJson({ account_login: "shabnam", account_type: "User" });
    const result = await api.installations.lookup(42);
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/me/installations/lookup/42");
    expect(result).toEqual({ account_login: "shabnam", account_type: "User" });
  });

  it("POSTs to /me/installations/sync for scope: me", async () => {
    stubOkJson({ synced: true, token_ref: "tok_x" });
    await api.installations.sync(
      { scope: "me" },
      { account_login: "shabnam", account_type: "User", installation_id: 42 },
    );
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/me/installations/sync");
    expect(JSON.parse(init.body as string)).toEqual({
      account_login: "shabnam",
      account_type: "User",
      installation_id: 42,
    });
  });

  it("POSTs to /orgs/{orgLogin}/installations/sync for scope: org", async () => {
    stubOkJson({ synced: true, token_ref: "tok_y" });
    await api.installations.sync(
      { scope: "org", orgLogin: "acme" },
      { account_login: "acme", account_type: "Organization", installation_id: 7 },
    );
    const [url] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/orgs/acme/installations/sync");
  });
});
