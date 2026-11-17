import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import type { MyOrgMembership } from "@/lib/api/types"

/** The active org scope's login when the caller has no Clevis membership there: a workspace admin can keep such a
 * scope through a saved token alone. Overview and Health & Security still work through that token (`/me/*`
 * routes), but every `/orgs/{login}/...` page answers 403. Null for personal scopes, for orgs the caller belongs
 * to, and until the membership list has loaded (same ["my-orgs"] key as the sidebar, so no extra request). */
export function useNonMemberOrg(): string | null {
  const { scope } = useActiveScope()
  const org = scope?.kind === "org" ? scope.login : ""
  const mine = useQuery<MyOrgMembership[]>({ queryKey: ["my-orgs"], queryFn: () => api.orgs.mine(), retry: false, enabled: org !== "" })
  if (org === "" || !mine.isSuccess) return null
  return mine.data.some((m) => m.org_login.toLowerCase() === org.toLowerCase()) ? null : org
}

/** Routes that work without a membership in the active org (they read through the token, not `/orgs/{login}`). */
export const MEMBERSHIP_FREE_ROUTES = new Set(["/", "/security", "/my"])
