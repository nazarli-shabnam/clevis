import { describe, expect, it } from "vitest";

import { effectiveInvitationStatus, invitationExpiry } from "@/lib/invitation-expiry";

const NOW = Date.parse("2026-10-01T12:00:00Z");
const at = (ms: number) => new Date(NOW + ms).toISOString();
const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("invitationExpiry", () => {
  it("rounds days up, so a fresh 7-day invitation reads 'in 7 days'", () => {
    expect(invitationExpiry({ status: "pending", expires_at: at(7 * DAY - 5000) }, NOW)).toEqual({ label: "in 7 days", tone: "normal" });
    expect(invitationExpiry({ status: "pending", expires_at: at(DAY) }, NOW)).toEqual({ label: "in 1 day", tone: "normal" });
    // just over a day left must not read as "1 day"
    expect(invitationExpiry({ status: "pending", expires_at: at(DAY + MIN) }, NOW)).toEqual({ label: "in 2 days", tone: "normal" });
  });

  it("flags an invitation as expiring soon inside the last day, counting hours then minutes", () => {
    expect(invitationExpiry({ status: "pending", expires_at: at(5 * HOUR + 30 * MIN) }, NOW)).toEqual({ label: "in 6 hours", tone: "soon" });
    expect(invitationExpiry({ status: "pending", expires_at: at(HOUR) }, NOW)).toEqual({ label: "in 1 hour", tone: "soon" });
    expect(invitationExpiry({ status: "pending", expires_at: at(45 * MIN) }, NOW)).toEqual({ label: "in 45 minutes", tone: "soon" });
    // never "in 0 minutes" and never "in 60 minutes"
    expect(invitationExpiry({ status: "pending", expires_at: at(10 * 1000) }, NOW)).toEqual({ label: "in 1 minute", tone: "soon" });
    expect(invitationExpiry({ status: "pending", expires_at: at(HOUR - 1000) }, NOW)).toEqual({ label: "in 59 minutes", tone: "soon" });
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

describe("effectiveInvitationStatus", () => {
  it("turns a lapsed pending invitation into expired and leaves everything else alone", () => {
    expect(effectiveInvitationStatus({ status: "pending", expires_at: at(-1) }, NOW)).toBe("expired");
    expect(effectiveInvitationStatus({ status: "pending", expires_at: at(DAY) }, NOW)).toBe("pending");
    expect(effectiveInvitationStatus({ status: "pending", expires_at: "garbage" }, NOW)).toBe("pending");
    expect(effectiveInvitationStatus({ status: "accepted", expires_at: at(-DAY) }, NOW)).toBe("accepted");
    expect(effectiveInvitationStatus({ status: "revoked", expires_at: at(-DAY) }, NOW)).toBe("revoked");
  });
});
