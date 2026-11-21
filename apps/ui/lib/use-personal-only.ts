import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useActiveScope } from "@/lib/active-scope"
import type { MyOrgMembership } from "@/lib/api/types"

/** Pages that only make sense for an organization: they list org repositories, members or notifications. */
export const ORG_ONLY_ROUTES = new Set(["/pulls", "/releases", "/notifications", "/collaborators"])

/** True for someone on a personal scope who belongs to no Clevis organization, so the org-only pages would just
 * be an empty state telling them to install the App on one. False until the membership list has loaded (same
 * ["my-orgs"] key as the sidebar, so no extra request), and for anyone with an org to switch to. */
export function usePersonalOnly(): boolean {
  const { scope } = useActiveScope()
  const mine = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
    retry: false,
    enabled: scope?.kind === "personal",
  })
  return scope?.kind === "personal" && mine.isSuccess && mine.data.length === 0
}
