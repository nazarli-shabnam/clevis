import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import type { DigestSettings } from "@/lib/api/types"

/**
 * The org's digest settings, which also report whether the server can send email at all (`smtp_configured`).
 * One shared query per org, so the members page, the SMTP notice and the digest card read the same cached answer.
 */
export function useDigestSettings(orgLogin: string, enabled = true) {
  return useQuery<DigestSettings>({
    queryKey: ["digest", orgLogin],
    queryFn: () => api.orgs.digest(orgLogin),
    retry: false,
    enabled,
  })
}
