import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"

/**
 * The GitHub App's slug (for install / review links), served by the API at runtime.
 * `NEXT_PUBLIC_GITHUB_APP_SLUG`, baked in at build time, is only a fallback for older deployments.
 * `appConfigured` is false when the API itself has no App credentials, which tells "not set up"
 * apart from "set up, but the slug couldn't be determined" (`slug` null while `appConfigured`).
 */
export function useGithubAppSlug(): { slug: string | null; appConfigured: boolean } {
  const { data } = useQuery({
    queryKey: ["github-app-slug"],
    queryFn: () => api.auth.setupRequired(),
    staleTime: 5 * 60_000,
    retry: false,
  })
  const slug = data?.github_app_slug || process.env.NEXT_PUBLIC_GITHUB_APP_SLUG || null
  return { slug, appConfigured: slug !== null || data?.github_app_configured === true }
}
