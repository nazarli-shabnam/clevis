import { describe, expect, it } from "vitest";

import { shouldApplyResolvedToken } from "@/lib/token-resolve";

describe("token-resolve", () => {
  it("ignores stale resolve responses for a different owner", () => {
    expect(shouldApplyResolvedToken("acme", "other-org")).toBe(false);
    expect(shouldApplyResolvedToken("acme", "acme")).toBe(true);
  });
});
