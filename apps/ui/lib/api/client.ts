import type {
  AnalyticsHistoryResponse,
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
  OrgEventsResponse,
  PendingInvitationSummary,
  RepoListResponse,
  RepoPullsResponse,
  RepoSecurityResponse,
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
  if (id === "repository_dependabot_alerts_clear" && typeof raw === "object" && raw !== null) {
    const r = raw as Record<string, unknown>
    return {
      type: "severity_counts",
      critical: Number(r.critical ?? 0),
      high: Number(r.high ?? 0),
      medium: Number(r.medium ?? 0),
      low: Number(r.low ?? 0),
    }
  }
  if (typeof raw === "object" && raw !== null) {
    const r = raw as Record<string, unknown>
    if ("checked" in r && "protected" in r) {
      return { type: "ratio", numerator: Number(r.protected), denominator: Number(r.checked) }
    }
    if ("enabled" in r && "total" in r) {
      return { type: "ratio", numerator: Number(r.enabled), denominator: Number(r.total) }
    }
    if ("open" in r && "repos_with_alerts" in r && "total_repos" in r) {
      const total = Number(r.total_repos)
      return { type: "ratio", numerator: total - Number(r.repos_with_alerts), denominator: total }
    }
    if ("repos_checked" in r && "force_push_allowed" in r) {
      const total = Number(r.repos_checked)
      return { type: "ratio", numerator: total - Number(r.force_push_allowed), denominator: total }
    }
  }
  return null
}

// ── API surface ───────────────────────────────────────────────────────────────

export const api = {
  analytics: {
    // Prefer omitting token — the API mints a GitHub App installation token when
    // the owner is connected. An explicit token remains supported for tests/tools.
    overview: async (owner: string, token?: string): Promise<AnalyticsOverviewResponse> => {
      const trimmed = token?.trim()
      const data = await post<AnalyticsOverviewResponse>("/me/analytics/overview", {
        owner,
        ...(trimmed ? { token: trimmed } : {}),
      })
      return {
        ...data,
        checks: data.checks.map((c) => ({ ...c, value: normalizeCheckValue(c.id, c.value) })),
      }
    },
    history: (owner: string) =>
      get<AnalyticsHistoryResponse>(`/me/analytics/history?owner=${encodeURIComponent(owner)}`),
  },
  cache: {
    list: (owner: string, repo: string, token?: string) => {
      const trimmed = token?.trim()
      return post<CacheListResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions-caches`,
        trimmed ? { token: trimmed } : {},
      )
    },
    clear: (
      owner: string,
      repo: string,
      body: { actor: string; dry_run: boolean; token?: string; key?: string; ref?: string },
    ) => {
      const trimmed = body.token?.trim()
      return post<CacheClearResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions-caches/clear`,
        {
          actor: body.actor,
          dry_run: body.dry_run,
          ...(trimmed ? { token: trimmed } : {}),
          ...(body.key !== undefined ? { key: body.key } : {}),
          ...(body.ref !== undefined ? { ref: body.ref } : {}),
        },
      )
    },
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
    security: (org: string, owner: string, repo: string, token: string) =>
      post<RepoSecurityResponse>(
        `/orgs/${encodeURIComponent(org)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/security`,
        { token: token || undefined },
      ),
  },
  jobs: {
    list: () => get<JobOut[]>("/jobs"),
  },
  github: {
    events: (org: string, token: string, perPage = 30) =>
      post<OrgEventsResponse>(`/github/orgs/${encodeURIComponent(org)}/events`, {
        token: token || undefined,
        per_page: perPage,
      }),
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
    revokeSessions: () => post<{ ok: boolean }>("/auth/me/revoke-sessions", {}),
  },
}
