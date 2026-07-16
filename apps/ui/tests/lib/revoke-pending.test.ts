import { describe, expect, it } from "vitest";

import { addRevokingId, isRevoking, removeRevokingId } from "@/lib/revoke-pending";

describe("revoke-pending", () => {
  it("tracks multiple concurrent revokes independently", () => {
    let pending = new Set<number>();

    pending = addRevokingId(pending, 1);
    pending = addRevokingId(pending, 2);

    expect(isRevoking(pending, 1)).toBe(true);
    expect(isRevoking(pending, 2)).toBe(true);

    pending = removeRevokingId(pending, 1);

    expect(isRevoking(pending, 1)).toBe(false);
    expect(isRevoking(pending, 2)).toBe(true);
  });
});
