import type { AnalyticsOverviewResponse, CacheListResponse, CacheClearResponse } from "./types"

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080"
// Role sent in X-Role header for privileged operations (e.g. cache clear).
// Configure via NEXT_PUBLIC_DEFAULT_ROLE; defaults to "viewer" so admins must opt-in explicitly.
const ROLE = process.env.NEXT_PUBLIC_DEFAULT_ROLE ?? "viewer"

async function post<T>(path: string, body: unknown, headers?: Record<string, string>): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  })
  const json = await res.json()
  if (!res.ok) throw new Error((json as { detail?: string }).detail ?? `Request failed: ${res.status}`)
  return json as T
}

export const api = {
  analytics: {
    overview: (owner: string, token: string) =>
      post<AnalyticsOverviewResponse>("/analytics/overview", { owner, token }),
  },
  cache: {
    list: (owner: string, repo: string, token: string) =>
      post<CacheListResponse>(`/repos/${owner}/${repo}/actions-caches`, { token }),
    clear: (
      owner: string,
      repo: string,
      body: { token: string; actor: string; dry_run: boolean; key?: string; ref?: string },
    ) =>
      post<CacheClearResponse>(`/repos/${owner}/${repo}/actions-caches/clear`, body, {
        "X-Role": ROLE,
      }),
  },
}
