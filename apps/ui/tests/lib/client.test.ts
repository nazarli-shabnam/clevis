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

describe("api.repos", () => {
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

  it("POSTs to /orgs/{org}/repos with token: undefined when the token field is empty", async () => {
    stubOkJson({ org: "acme", total: 0, repos: [] });
    const result = await api.repos.list("acme", "");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/orgs/acme/repos");
    expect(JSON.parse(init.body as string)).toEqual({ token: undefined });
    expect(result).toEqual({ org: "acme", total: 0, repos: [] });
  });

  it("POSTs to /orgs/{org}/repos/{owner}/{repo}/stats", async () => {
    stubOkJson({ repository: "acme/demo", commit_activity: [], participation: {}, contributors: [] });
    await api.repos.stats("acme", "acme", "demo", "ghp_test");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/orgs/acme/repos/acme/demo/stats");
    expect(JSON.parse(init.body as string)).toEqual({ token: "ghp_test" });
  });

  it("POSTs to /orgs/{org}/repos/{owner}/{repo}/pulls", async () => {
    stubOkJson({ repository: "acme/demo", total: 0, pulls: [] });
    await api.repos.pulls("acme", "acme", "demo", "");
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(String(url)).toContain("/orgs/acme/repos/acme/demo/pulls");
    expect(JSON.parse(init.body as string)).toEqual({ token: undefined });
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
