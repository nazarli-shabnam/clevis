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
