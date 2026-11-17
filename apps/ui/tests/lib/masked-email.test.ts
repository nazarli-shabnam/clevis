import { describe, expect, it } from "vitest";

import { matchesMaskedEmail } from "@/lib/masked-email";

describe("matchesMaskedEmail", () => {
  it("matches on first letter and domain, ignoring case", () => {
    expect(matchesMaskedEmail("j***@acme.com", "jane@acme.com")).toBe(true);
    expect(matchesMaskedEmail("j***@acme.com", "J.Doe@ACME.com")).toBe(true);
  });

  it("does not match a different first letter or domain", () => {
    expect(matchesMaskedEmail("j***@acme.com", "kate@acme.com")).toBe(false);
    expect(matchesMaskedEmail("j***@acme.com", "jane@acme.org")).toBe(false);
    expect(matchesMaskedEmail("j***@acme.com", "jane@mail.acme.com")).toBe(false);
  });

  it("does not match an address without a domain", () => {
    expect(matchesMaskedEmail("j***@acme.com", "jane")).toBe(false);
  });
});
