import type { ActiveScope } from "@/lib/active-scope"
import type { MyOrgMembership } from "@/lib/api/types"

const membersPath = (m: MyOrgMembership) => `/settings/org/${encodeURIComponent(m.org_login)}/members`

function adminOrgFor(memberships: MyOrgMembership[], scope: ActiveScope | null): MyOrgMembership | undefined {
  const adminOrgs = memberships.filter((m) => m.role === "admin")
  return adminOrgs.find((m) => scope?.kind === "org" && m.org_login === scope.login) ?? adminOrgs[0]
}

// Where to send someone to invite people. Inviting is admin-only on the backend, so this is the
// active-scope org if admin, else the first admin org, else /settings.
export function inviteMembersHref(memberships: MyOrgMembership[], scope: ActiveScope | null): string {
  const target = adminOrgFor(memberships, scope)
  return target ? membersPath(target) : "/settings"
}

// Where to send someone to see the member roster. Any org member may read it (only inviting is
// admin-only), so prefer an org they admin, then the active-scope org, then any membership, and only
// then /settings.
export function membersHref(memberships: MyOrgMembership[], scope: ActiveScope | null): string {
  const target =
    adminOrgFor(memberships, scope) ??
    memberships.find((m) => scope?.kind === "org" && m.org_login === scope.login) ??
    memberships[0]
  return target ? membersPath(target) : "/settings"
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
