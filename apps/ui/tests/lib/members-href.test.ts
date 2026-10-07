import { describe, expect, it } from "vitest"
import { inviteMembersHref, membersHref, orgRoleFor } from "@/lib/members-href"
import type { MyOrgMembership } from "@/lib/api/types"

const admin = (org_login: string): MyOrgMembership => ({ org_login, role: "admin" })
const member = (org_login: string): MyOrgMembership => ({ org_login, role: "member" })

describe("membersHref", () => {
  it("prefers the admin org matching the active org scope", () => {
    expect(
      membersHref([admin("acme"), admin("widgets-inc")], { kind: "org", login: "widgets-inc" }),
    ).toBe("/settings/org/widgets-inc/members")
  })

  it("falls back to the first admin org when the scope org isn't one the user admins", () => {
    expect(
      membersHref([member("acme"), admin("widgets-inc")], { kind: "org", login: "acme" }),
    ).toBe("/settings/org/widgets-inc/members")
  })

  it("falls back to the first admin org when the scope is personal", () => {
    expect(membersHref([admin("acme")], { kind: "personal", login: "octocat" })).toBe(
      "/settings/org/acme/members",
    )
  })

  it("sends a plain member to the active org's roster instead of /settings", () => {
    expect(
      membersHref([member("acme"), member("widgets-inc")], { kind: "org", login: "widgets-inc" }),
    ).toBe("/settings/org/widgets-inc/members")
  })

  it("falls back to any membership when the user admins no org and the scope isn't one of theirs", () => {
    expect(membersHref([member("acme")], { kind: "personal", login: "octocat" })).toBe("/settings/org/acme/members")
    expect(membersHref([member("acme")], null)).toBe("/settings/org/acme/members")
  })

  it("returns /settings only when the user belongs to no org", () => {
    expect(membersHref([], { kind: "org", login: "acme" })).toBe("/settings")
    expect(membersHref([], null)).toBe("/settings")
  })

  it("encodes org logins with URL-unsafe characters", () => {
    expect(membersHref([admin("a/b")], null)).toBe("/settings/org/a%2Fb/members")
  })
})

describe("inviteMembersHref", () => {
  it("only ever points at an org the user admins, since inviting is admin-only", () => {
    expect(inviteMembersHref([member("acme"), admin("widgets-inc")], { kind: "org", login: "acme" })).toBe(
      "/settings/org/widgets-inc/members",
    )
    expect(inviteMembersHref([admin("acme")], { kind: "org", login: "acme" })).toBe("/settings/org/acme/members")
  })

  it("stays on /settings for a plain member or someone with no org", () => {
    expect(inviteMembersHref([member("acme")], { kind: "org", login: "acme" })).toBe("/settings")
    expect(inviteMembersHref([], null)).toBe("/settings")
  })
})

describe("orgRoleFor", () => {
  it("returns the caller's role in the org", () => {
    expect(orgRoleFor([admin("acme"), member("widgets-inc")], "acme")).toBe("admin")
    expect(orgRoleFor([admin("acme"), member("widgets-inc")], "widgets-inc")).toBe("member")
  })

  it("matches logins case-insensitively", () => {
    expect(orgRoleFor([admin("Acme")], "acme")).toBe("admin")
  })

  it("returns null for an owner the caller has no Clevis membership in", () => {
    expect(orgRoleFor([admin("acme")], "octocat")).toBeNull()
    expect(orgRoleFor([], "acme")).toBeNull()
  })
})
