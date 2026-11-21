import { describe, expect, it } from "vitest";

import { runBulk } from "@/lib/bulk";
import { matchesExpiry } from "@/lib/expiry-filter";

describe("runBulk", () => {
  it("runs every id and reports the failures instead of throwing", async () => {
    const seen: number[] = [];
    const failed = await runBulk([1, 2, 3, 4, 5, 6, 7], async (id) => {
      seen.push(id);
      if (id === 3 || id === 7) throw new Error(`bad ${id}`);
    });
    expect(seen.sort()).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(failed).toEqual([{ id: 3, message: "bad 3" }, { id: 7, message: "bad 7" }]);
  });
});

describe("matchesExpiry", () => {
  const now = Date.parse("2026-06-01T00:00:00Z");
  const inDays = (d: number) => new Date(now + d * 86_400_000).toISOString();

  it("treats never-expiring as matching only 'any'", () => {
    expect(matchesExpiry(null, "any", now)).toBe(true);
    expect(matchesExpiry(null, "30", now)).toBe(false);
    expect(matchesExpiry(null, "expired", now)).toBe(false);
  });

  it("splits expired from expiring-within-N", () => {
    expect(matchesExpiry(inDays(-1), "expired", now)).toBe(true);
    expect(matchesExpiry(inDays(-1), "7", now)).toBe(false);
    expect(matchesExpiry(inDays(3), "7", now)).toBe(true);
    expect(matchesExpiry(inDays(10), "7", now)).toBe(false);
    expect(matchesExpiry(inDays(10), "14", now)).toBe(true);
  });
});
