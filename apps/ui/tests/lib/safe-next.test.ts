import { afterEach, describe, expect, it } from "vitest";

import { currentLocationNext, safeNextPath, withNext } from "@/lib/safe-next";

describe("safeNextPath", () => {
  it("keeps same-site relative paths, including a query string", () => {
    expect(safeNextPath("/invite/abc")).toBe("/invite/abc");
    expect(safeNextPath("/settings/github-callback?installation_id=1&setup_action=install")).toBe(
      "/settings/github-callback?installation_id=1&setup_action=install",
    );
  });

  it("falls back to / for anything that could leave the site", () => {
    for (const bad of [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "/a\nb", "relative"]) {
      expect(safeNextPath(bad)).toBe("/");
    }
  });
});

describe("withNext", () => {
  it("attaches an encoded destination, and nothing for the default /", () => {
    expect(withNext("/login", "/a?b=1&c=2")).toBe("/login?next=%2Fa%3Fb%3D1%26c%3D2");
    expect(withNext("/login", "/")).toBe("/login");
  });
});

describe("currentLocationNext", () => {
  afterEach(() => window.history.pushState({}, "", "/"));

  it("is the current path plus query string", () => {
    window.history.pushState({}, "", "/settings/github-callback?installation_id=123&setup_action=install");
    expect(currentLocationNext()).toBe("/settings/github-callback?installation_id=123&setup_action=install");
  });
});
