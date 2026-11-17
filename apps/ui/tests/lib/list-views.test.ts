import { describe, expect, it } from "vitest";

import { auditViewCodec, DEFAULT_MEMBERS_VIEW, DEFAULT_PULL_VIEW, membersViewCodec, pullViewCodec } from "@/lib/list-views";
import { EMPTY_FORM } from "@/lib/audit-filters";

describe("list view URL codecs (#1128)", () => {
  it("keeps an unfiltered view out of the URL", () => {
    expect(pullViewCodec.toParams(DEFAULT_PULL_VIEW).toString()).toBe("");
    expect(membersViewCodec.toParams(DEFAULT_MEMBERS_VIEW).toString()).toBe("");
    expect(auditViewCodec.toParams({ applied: EMPTY_FORM, action: "" }).toString()).toBe("");
  });

  it("round-trips a filtered view", () => {
    const pulls = { status: "stale", author: "octo cat", sort: "oldest", group: "author" } as const;
    expect(pullViewCodec.fromParams(pullViewCodec.toParams(pulls))).toEqual(pulls);

    const members = { search: "ali", role: "admin", twoFaOff: true } as const;
    expect(membersViewCodec.fromParams(membersViewCodec.toParams(members))).toEqual(members);

    const audit = { applied: { actionPrefix: "waiver.", actor: "a@b.c", target: "acme", from: "2026-01-01", to: "2026-01-31" }, action: "waiver.create" };
    expect(auditViewCodec.fromParams(auditViewCodec.toParams(audit))).toEqual(audit);
  });

  it("falls back to the defaults for an unknown value", () => {
    expect(pullViewCodec.fromParams(new URLSearchParams("status=bogus&sort=nope&group=x"))).toEqual(DEFAULT_PULL_VIEW);
    expect(membersViewCodec.fromParams(new URLSearchParams("role=owner&no2fa=yes"))).toEqual(DEFAULT_MEMBERS_VIEW);
  });
});
