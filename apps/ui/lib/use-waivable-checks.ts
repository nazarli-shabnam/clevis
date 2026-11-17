import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"

/** The API's waivable-check catalog for an org (cached for the session: it only changes with a deploy). */
export function useWaivableChecks(org: string | undefined) {
  const query = useQuery({
    queryKey: ["waivable-checks", org],
    queryFn: () => api.waivers.checks(org!),
    enabled: !!org,
    staleTime: Infinity,
  })
  const checks = query.data ?? []
  return {
    checks,
    loaded: query.isSuccess,
    isError: query.isError,
    isWaivable: (id: string) => checks.some((c) => c.id === id),
    isOrgWideOnly: (id: string) => checks.find((c) => c.id === id)?.org_wide_only ?? false,
    titleOf: (id: string) => checks.find((c) => c.id === id)?.title ?? id,
  }
}
