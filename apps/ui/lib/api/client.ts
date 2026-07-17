import type {
  AnalyticsOverviewResponse,
  AuditLogOut,
  CacheClearResponse,
  CacheListResponse,
  CheckValue,
  InstallationLookup,
  InstallationMeta,
  InvitationCreateResponse,
  InvitationOut,
  InvitationPreview,
  JobOut,
  MyOrgMembership,
  PendingInvitationSummary,
  RepoListResponse,
  RepoPullsResponse,
  RepoStatsResponse,
  SavedTokenMeta,
  SyncInstallationsResponse,
} from "./types"

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080"

// ── Token accessor ────────────────────────────────────────────────────────────
// The auth context writes the JWT to localStorage under this key.
// The client reads it on each request so it's always fresh after login.
const _TOKEN_KEY = "clevis:token"

function getAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {}
  const token = localStorage.getItem(_TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────

// Hard ceiling on every request. Without this, a fetch to an unreachable/hanging
// API never settles, leaving callers (e.g. React Query) stuck in a loading state
// forever instead of surfacing an error.
const REQUEST_TIMEOUT_MS = 15000

async function fetchWithTimeout(url: string, init: RequestInit = {}): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    // credentials:"include" sends the httpOnly session cookie (GitHub OAuth sessions) alongside
    // the Bearer header (email/password sessions); require_auth accepts either.
    return await fetch(url, { credentials: "include", ...init, signal: controller.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Request timed out after ${REQUEST_TIMEOUT_MS / 1000}s — is the API reachable?`)
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  const json = await res.json().catch(() => null)
  if (res.status === 401) {
    // Clear stale token and let the auth guard redirect to /login
    if (typeof window !== "undefined") localStorage.removeItem(_TOKEN_KEY)
    window.dispatchEvent(new Event("clevis:unauthorized"))
  }
  if (!res.ok) throw new Error((json as { detail?: string } | null)?.detail ?? `Request failed: ${res.status}`)
  return json as T
}

async function post<T>(path: string, body: unknown, extraHeaders?: Record<string, string>): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders(), ...extraHeaders },
    body: JSON.stringify(body),
  })
  return handleResponse<T>(res)
}

async function get<T>(path: string): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
  })
  return handleResponse<T>(res)
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleResponse<T>(res)
}

async function patch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleResponse<T>(res)
}

async function del(path: string): Promise<void> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() },
  })
  if (res.status === 401) {
    if (typeof window !== "undefined") localStorage.removeItem(_TOKEN_KEY)
    window.dispatchEvent(new Event("clevis:unauthorized"))
  }
  if (!res.ok) {
    const json = await res.json().catch(() => ({}))
    throw new Error((json as { detail?: string }).detail ?? `Request failed: ${res.status}`)
  }
}

// ── Check value normalization ─────────────────────────────────────────────────

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

// ── API surface ───────────────────────────────────────────────────────────────

export const api = {
  analytics: {
    // token is optional — the API falls back to a connected GitHub App installation
    // token when one exists for this owner, so an empty field is fine to send.
    overview: async (owner: string, token: string): Promise<AnalyticsOverviewResponse> => {
      const data = await post<AnalyticsOverviewResponse>("/me/analytics/overview", { owner, token: token || undefined })
      return {
        ...data,
        checks: data.checks.map((c) => ({ ...c, value: normalizeCheckValue(c.id, c.value) })),
      }
    },
  },
  cache: {
    list: (owner: string, repo: string, token: string) =>
      post<CacheListResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions-caches`,
        { token: token || undefined },
      ),
    clear: (
      owner: string,
      repo: string,
      body: { token: string; actor: string; dry_run: boolean; key?: string; ref?: string },
    ) =>
      post<CacheClearResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions-caches/clear`,
        { ...body, token: body.token || undefined },
      ),
  },
  repos: {
    list: (org: string, token: string) =>
      post<RepoListResponse>(`/orgs/${encodeURIComponent(org)}/repos`, { token: token || undefined }),
    stats: (org: string, owner: string, repo: string, token: string) =>
      post<RepoStatsResponse>(
        `/orgs/${encodeURIComponent(org)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/stats`,
        { token: token || undefined },
      ),
    pulls: (org: string, owner: string, repo: string, token: string) =>
      post<RepoPullsResponse>(
        `/orgs/${encodeURIComponent(org)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pulls`,
        { token: token || undefined },
      ),
  },
  jobs: {
    list: () => get<JobOut[]>("/jobs"),
  },
  audit: {
    list: (action?: string) =>
      get<AuditLogOut[]>(`/audit${action ? `?action=${encodeURIComponent(action)}` : ""}`),
  },
  installations: {
    list: () => get<InstallationMeta[]>("/me/installations"),
    lookup: (installationId: number) =>
      get<InstallationLookup>(`/me/installations/lookup/${installationId}`),
    sync: (
      target: { scope: "me" } | { scope: "org"; orgLogin: string },
      body: { account_login: string; account_type: string; installation_id: number },
    ) =>
      target.scope === "me"
        ? post<SyncInstallationsResponse>("/me/installations/sync", body)
        : post<SyncInstallationsResponse>(
            `/orgs/${encodeURIComponent(target.orgLogin)}/installations/sync`,
            body,
          ),
  },
  orgs: {
    mine: () => get<MyOrgMembership[]>("/me/orgs"),
  },
  invitations: {
    create: (orgLogin: string, email: string) =>
      post<InvitationCreateResponse>(`/orgs/${encodeURIComponent(orgLogin)}/invitations`, { email }),
    list: (orgLogin: string) => get<InvitationOut[]>(`/orgs/${encodeURIComponent(orgLogin)}/invitations`),
    revoke: (orgLogin: string, invitationId: number) =>
      post<InvitationOut>(`/orgs/${encodeURIComponent(orgLogin)}/invitations/${invitationId}/revoke`, {}),
    preview: (token: string) => get<InvitationPreview>(`/invitations/${encodeURIComponent(token)}`),
    accept: (token: string) => post<{ org_login: string; role: string }>(`/invitations/${encodeURIComponent(token)}/accept`, {}),
  },
  tokens: {
    list: () => get<SavedTokenMeta[]>("/tokens"),
    upsert: (org: string, token: string, label?: string) =>
      put<SavedTokenMeta>(`/tokens/${encodeURIComponent(org)}`, { token, label }),
    resolve: (org: string) =>
      post<{ token: string }>("/tokens/resolve", { org }),
    delete: (org: string) => del(`/tokens/${encodeURIComponent(org)}`),
  },
  config: {
    getAll: () => get<Record<string, string>>("/config"),
    update: (key: string, value: string) =>
      put<Record<string, string>>(`/config/${encodeURIComponent(key)}`, { value }),
  },
  auth: {
    setupRequired: () => get<{ setup_required: boolean }>("/auth/setup-required"),
    setup: (email: string, password: string, name?: string) =>
      post<{ access_token: string; user: { id: number; email: string; name: string | null; is_workspace_admin: boolean } }>(
        "/auth/setup",
        { email, password, name },
      ),
    register: (email: string, password: string, name?: string) =>
      post<{
        access_token: string
        user: { id: number; email: string; name: string | null; is_workspace_admin: boolean }
        pending_invitations: PendingInvitationSummary[]
      }>("/auth/register", { email, password, name }),
    patchMe: (name: string) =>
      patch<{ id: number; email: string; name: string | null; is_workspace_admin: boolean }>("/auth/me", { name }),
  },
}
