import type { ActiveScope } from "@/lib/active-scope"
import type { MyOrgMembership } from "@/lib/api/types"

// Resolves the members page for the current user. It's admin-only on the backend, so prefer the
// active-scope org if admin, else the first admin org, else /settings.
export function membersHref(memberships: MyOrgMembership[], scope: ActiveScope | null): string {
  const adminOrgs = memberships.filter((m) => m.role === "admin")
  const target =
    adminOrgs.find((m) => scope?.kind === "org" && m.org_login === scope.login) ?? adminOrgs[0]
  return target ? `/settings/org/${encodeURIComponent(target.org_login)}/members` : "/settings"
}

// The caller's role in `owner`'s org, or null when they have no Clevis membership there (a personal
// account, an org Clevis hasn't connected, or one they don't belong to). Case-insensitive, like GitHub logins.
export function orgRoleFor(memberships: MyOrgMembership[], owner: string): MyOrgMembership["role"] | null {
  return memberships.find((x) => x.org_login.toLowerCase() === owner.toLowerCase())?.role ?? null
}

// True when the caller is a plain (non-admin) member of `owner`'s org, so org-admin-only
// actions (Fix this, File as issue, nudges) would just 403. Unknown owners stay allowed:
// the API may still accept a caller-supplied token for an org Clevis hasn't connected.
export function isOrgMemberOnly(memberships: MyOrgMembership[], owner: string): boolean {
  const m = memberships.find((x) => x.org_login.toLowerCase() === owner.toLowerCase())
  return !!m && m.role !== "admin"
}
