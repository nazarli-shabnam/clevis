import { describe, expect, it } from "vitest";

import { invitationExpiry } from "@/lib/invitation-expiry";

const NOW = Date.parse("2026-10-01T12:00:00Z");
const at = (ms: number) => new Date(NOW + ms).toISOString();
const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("invitationExpiry", () => {
  it("shows days left for a pending invitation that isn't close to expiring", () => {
    expect(invitationExpiry({ status: "pending", expires_at: at(6 * DAY + HOUR) }, NOW)).toEqual({ label: "in 6 days", tone: "normal" });
    expect(invitationExpiry({ status: "pending", expires_at: at(DAY) }, NOW)).toEqual({ label: "in 1 day", tone: "normal" });
  });

  it("flags an invitation as expiring soon inside the last day, counting hours then minutes", () => {
    expect(invitationExpiry({ status: "pending", expires_at: at(5 * HOUR + 10 * MIN) }, NOW)).toEqual({ label: "in 5 hours", tone: "soon" });
    expect(invitationExpiry({ status: "pending", expires_at: at(HOUR) }, NOW)).toEqual({ label: "in 1 hour", tone: "soon" });
    expect(invitationExpiry({ status: "pending", expires_at: at(45 * MIN) }, NOW)).toEqual({ label: "in 45 minutes", tone: "soon" });
    // never "in 0 minutes"
    expect(invitationExpiry({ status: "pending", expires_at: at(10 * 1000) }, NOW)).toEqual({ label: "in 1 minute", tone: "soon" });
  });

  it("reads as expired when the server says so or the time has passed, even if still marked pending", () => {
    expect(invitationExpiry({ status: "expired", expires_at: at(-3 * DAY) }, NOW).tone).toBe("expired");
    const stale = invitationExpiry({ status: "pending", expires_at: at(-1) }, NOW);
    expect(stale.tone).toBe("expired");
    expect(stale.label).toMatch(/^expired /);
  });

  it("has no clock for accepted or revoked invitations, or an unreadable date", () => {
    expect(invitationExpiry({ status: "accepted", expires_at: at(-DAY) }, NOW)).toEqual({ label: "—", tone: "none" });
    expect(invitationExpiry({ status: "revoked", expires_at: at(DAY) }, NOW)).toEqual({ label: "—", tone: "none" });
    expect(invitationExpiry({ status: "pending", expires_at: "garbage" }, NOW)).toEqual({ label: "—", tone: "none" });
  });
});
