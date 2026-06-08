import type {
  AnalyticsOverviewResponse,
  AuditLogOut,
  CacheClearResponse,
  CacheListResponse,
  CheckValue,
  InstallationMeta,
  JobOut,
  SavedTokenMeta,
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
    ) => post<CacheClearResponse>(`/repos/${owner}/${repo}/actions-caches/clear`, body),
  },
  jobs: {
    list: () => get<JobOut[]>("/jobs"),
  },
  audit: {
    list: (action?: string) =>
      get<AuditLogOut[]>(`/audit${action ? `?action=${encodeURIComponent(action)}` : ""}`),
  },
  installations: {
    list: () => get<InstallationMeta[]>("/github/app/installations"),
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
      post<{ access_token: string; user: { id: number; email: string; name: string | null; is_owner: boolean } }>(
        "/auth/setup",
        { email, password, name },
      ),
    patchMe: (name: string) =>
      patch<{ id: number; email: string; name: string | null; is_owner: boolean }>("/auth/me", { name }),
  },
}
