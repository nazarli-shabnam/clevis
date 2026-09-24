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
