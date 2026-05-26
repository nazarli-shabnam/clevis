import type { AnalyticsOverviewResponse, AuditLogOut, CacheListResponse, CacheClearResponse, CheckValue, JobOut, SavedTokenMeta } from "./types"

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
  const json = await res.json().catch(() => null)
  if (!res.ok) throw new Error((json as { detail?: string } | null)?.detail ?? `Request failed: ${res.status}`)
  return json as T
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
  })
  const json = await res.json().catch(() => null)
  if (!res.ok) throw new Error((json as { detail?: string } | null)?.detail ?? `Request failed: ${res.status}`)
  return json as T
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  const json = await res.json().catch(() => null)
  if (!res.ok) throw new Error((json as { detail?: string } | null)?.detail ?? `Request failed: ${res.status}`)
  return json as T
}

async function del(path: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" })
  if (!res.ok) {
    const json = await res.json().catch(() => ({}))
    throw new Error((json as { detail?: string }).detail ?? `Request failed: ${res.status}`)
  }
}

function normalizeCheckValue(id: string, raw: unknown): CheckValue {
  if (id === "organization_members_mfa_required") {
    return { type: "boolean", enabled: Boolean(raw) }
  }
  if (typeof raw === "object" && raw !== null) {
    const r = raw as Record<string, unknown>
    if ("checked" in r && "protected" in r) {
      return { type: "ratio", numerator: Number(r.protected), denominator: Number(r.checked) }
    }
    if ("enabled" in r && "total" in r) {
      return { type: "ratio", numerator: Number(r.enabled), denominator: Number(r.total) }
    }
  }
  return null
}

export const api = {
  analytics: {
    overview: async (owner: string, token: string): Promise<AnalyticsOverviewResponse> => {
      const data = await post<AnalyticsOverviewResponse>("/analytics/overview", { owner, token })
      return {
        ...data,
        checks: data.checks.map((c) => ({ ...c, value: normalizeCheckValue(c.id, c.value) })),
      }
    },
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
  jobs: {
    list: () => get<JobOut[]>("/jobs"),
  },
  audit: {
    list: (action?: string) =>
      get<AuditLogOut[]>(`/audit${action ? `?action=${encodeURIComponent(action)}` : ""}`),
  },
  tokens: {
    list: () => get<SavedTokenMeta[]>("/tokens"),
    upsert: (org: string, token: string, label?: string) =>
      put<SavedTokenMeta>(`/tokens/${encodeURIComponent(org)}`, { token, label }),
    resolve: (org: string) =>
      post<{ token: string }>("/tokens/resolve", { org }),
    delete: (org: string) => del(`/tokens/${encodeURIComponent(org)}`),
  },
}
