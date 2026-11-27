import type {
  WaivableCheck,
  GithubAppManifest,
  GithubAppStatus,
  MyInvitation,
  PendingInstallation,
  NotificationDestination,
  NotificationDestinationInput,
  NotificationDestinationPatch,
  ApiToken,
  ApiTokenCreated,
  ScanChanges,
  AutomationSchedules,
  DigestPreview,
  DigestSettings,
  ScheduledScanSettings,
  MemberAccess,
  CheckWaiver,
  CheckWaiverInput,
  WaiverRequest,
  WaiverRequestInput,
  NotificationFeed,
  NotificationKind,
  NotificationPreferences,
  ActionsUsageResponse,
  CacheSizePoint,
  AnalyticsHistoryResponse,
  AnalyticsOverviewResponse,
  LatestScanResponse,
  OrgMembership,
  IngestionHealth,
  AdminAuditFilters,
  AuditLogOut,
  OrgAuditFilters,
  BranchProtectionBulkResponse,
  BranchProtectionPreset,
  BulkRemediateResponse,
  SavedBranchProtectionPreset,
  CacheClearResponse,
  CacheListResponse,
  CheckValue,
  CockpitResponse,
  CreateIssueResponse,
  DependabotTriageResponse,
  DispatchResponse,
  DispatchAllResponse,
  ActivitySummary,
  FailedRunsResponse,
  GithubMembershipStatus,
  GithubOrgInvitationsResponse,
  GithubOrgMembersResponse,
  GithubOutsideCollaboratorsResponse,
  InactiveMembersResponse,
  InstallationLookup,
  InstallationMeta,
  InvitationBulkResponse,
  InvitationCreateResponse,
  InvitationOut,
  InvitationPreview,
  JobOut,
  HygieneScoringSettings,
  ControlMapping,
  ScoringExclusions,
  MyOrgMembership,
  MyIssueListResponse,
  MyPrListResponse,
  MyViewResponse,
  OrgEventsResponse,
  PendingInvitationSummary,
  PermissionAuditResponse,
  PrNudgeResponse,
  ReleaseTimelineResponse,
  RepoListResponse,
  RepoFlowMetricsResponse,
  RepoPullsResponse,
  RepoSecurityResponse,
  DependabotBurndown,
  RepoStatsResponse,
  RunsResponse,
  SavedTokenMeta,
  ScanExportResponse,
  SecretScanningResponse,
  RepoSecurityRow,
  SecurityMatrixResponse,
  UserSessionOut,
  SyncInstallationsResponse,
  WorkflowLintResponse,
  WorkflowsResponse,
} from "./types"
import { describeJob, forgetJob, jobGeneration, markLive, trackJob, unmarkLive } from "@/lib/running-jobs"
import { safeStorage } from "@/lib/safe-storage"

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080"

// The auth context writes the JWT here; read per request so it's fresh after login.
const _TOKEN_KEY = "clevis:token"

function getAuthHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {}
  const token = safeStorage.getItem(_TOKEN_KEY)
  return token ? { Authorization: `Bearer ${token}` } : {}
}


/** Opens the activity-summary SSE stream. Not routed through fetchWithTimeout: the stream is meant to
 * stay open (the API ends it after ~15 min), so only the caller's signal may abort it. Sends the same
 * credentials as every other call (Bearer header for password sessions, cookie for GitHub OAuth). */
export async function openActivityStream(org: string, days: number, signal: AbortSignal): Promise<Response> {
  const res = await fetch(`${BASE}/github/orgs/${encodeURIComponent(org)}/activity-summary/stream?days=${days}`, {
    credentials: "include",
    headers: { Accept: "text/event-stream", ...getAuthHeaders() },
    signal,
  })
  if (res.status === 401) notifyUnauthorized()
  return res
}

// Hard ceiling so a hanging API surfaces an error instead of leaving callers loading forever.
const REQUEST_TIMEOUT_MS = 15000
// Scans and bulk automations loop over every repo, so the API runs them as jobs (`?async=1` answers 202 with a
// job id straight away) and `postJob` polls the job. Each request stays short; only the wait is long.
const JOB_POLL_MS = 2000
const JOB_GIVE_UP_MS = 30 * 60_000

export async function fetchWithTimeout(
  url: string,
  init: RequestInit = {},
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    // credentials:"include" sends the httpOnly session cookie (GitHub OAuth sessions) alongside
    // the Bearer header (email/password sessions); require_auth accepts either.
    return await fetch(url, { credentials: "include", ...init, signal: controller.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      // A timeout says nothing about whether the server finished: a long request keeps running after the
      // browser gives up, so the message must not suggest it failed (or invite a blind retry).
      throw new Error(
        `Request timed out after ${timeoutMs / 1000}s. The server may still be working on it, so check the result before trying again.`,
      )
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

// FastAPI sends `detail` as a string for HTTPException but as a [{loc, msg, type}] array for request
// validation (422); `new Error(array)` would read "[object Object]".
export function errorDetail(json: unknown, fallback: string): string {
  const detail = (json as { detail?: unknown } | null)?.detail
  if (typeof detail === "string" && detail) return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (typeof d === "string" ? d : (d as { msg?: unknown } | null)?.msg))
      .filter((m): m is string => typeof m === "string" && m !== "")
    if (msgs.length) return msgs.join("; ")
  }
  return fallback
}

/**
 * GET a file the server builds (an export), with a long timeout. Resolves with the blob, the filename the
 * server chose and whether it flagged the file as cut off at its row cap (`truncatedHeader` = "true").
 */
async function downloadFile(
  path: string,
  qs: URLSearchParams,
  opts: { fallbackName: string; truncatedHeader: string; timeoutMessage: string },
): Promise<{ blob: Blob; filename: string; truncated: boolean }> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 120_000)
  try {
    const res = await fetch(`${BASE}${path}${qs.size ? `?${qs}` : ""}`, {
      credentials: "include",
      headers: { ...getAuthHeaders() },
      signal: controller.signal,
    })
    if (!res.ok) {
      const json = await res.json().catch(() => null)
      if (res.status === 401) notifyUnauthorized()
      throw new ApiError(errorDetail(json, `Request failed: ${res.status}`), res.status)
    }
    const filename = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? opts.fallbackName
    return { blob: await res.blob(), filename, truncated: res.headers.get(opts.truncatedHeader) === "true" }
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw new Error(opts.timeoutMessage)
    throw err
  } finally {
    clearTimeout(timer)
  }
}

// An Error that also carries the HTTP status, so a caller can treat one specific status as data.
class ApiError extends Error {
  // `code`: a stable machine-readable reason some endpoints add next to `detail` (branch on it, not on the text).
  constructor(message: string, readonly status: number, readonly code?: string) {
    super(message)
  }
}

/** Clears the stale token and lets the auth guard redirect to /login. */
function notifyUnauthorized() {
  if (typeof window === "undefined") return
  safeStorage.removeItem(_TOKEN_KEY)
  window.dispatchEvent(new Event("clevis:unauthorized"))
}

async function handleResponse<T>(res: Response): Promise<T> {
  const json = await res.json().catch(() => null)
  if (res.status === 401) notifyUnauthorized()
  if (!res.ok) {
    const code = (json as { code?: unknown } | null)?.code
    throw new ApiError(errorDetail(json, `Request failed: ${res.status}`), res.status, typeof code === "string" ? code : undefined)
  }
  return json as T
}

async function post<T>(
  path: string,
  body: unknown,
  extraHeaders?: Record<string, string>,
): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders(), ...extraHeaders },
    body: JSON.stringify(body),
  })
  return handleResponse<T>(res)
}

/**
 * The job was started but the client stopped waiting for it (the polls kept failing, or it ran past the give-up
 * time). The server may still be working, so a caller must not simply start the operation again: it carries the
 * job id so the job can be re-attached with `awaitJob`.
 */
export class JobStillRunningError extends Error {
  constructor(
    message: string,
    readonly jobId: number,
  ) {
    super(message)
    this.name = "JobStillRunningError"
  }
}

// Consecutive failed polls tolerated before giving up. The job id is known and the job keeps running
// server-side, so a network blip or a 503 on one poll must not abandon it.
const JOB_MAX_POLL_FAILURES = 5

// A poll failure worth retrying: no response at all (network error, timeout) or a server-side/throttling status.
// A 4xx (the job is gone, the session ended) will not fix itself.
function isTransientPollError(err: unknown): boolean {
  if (err instanceof ApiError) return err.status >= 500 || err.status === 429 || err.status === 408
  return true
}

function abortError(): DOMException {
  return new DOMException("The wait for the job was cancelled", "AbortError")
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(abortError())
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError())
    }
    signal?.addEventListener("abort", onAbort, { once: true })
  })
}

/**
 * Polls job `jobId` until it ends and resolves with its parsed result (rejects with its message when it failed).
 * Survives a few consecutive failed polls, stops when `signal` aborts (rejecting with an `AbortError`), and
 * throws `JobStillRunningError` when it gives up while the job may still be running.
 */
export async function awaitJob<T>(jobId: number, opts: { signal?: AbortSignal } = {}): Promise<T> {
  const { signal } = opts
  const deadline = Date.now() + JOB_GIVE_UP_MS
  let failures = 0
  for (;;) {
    await sleep(JOB_POLL_MS, signal)
    let job: JobOut | undefined
    try {
      job = await get<JobOut>(`/jobs/${jobId}`)
      failures = 0
    } catch (err) {
      if (signal?.aborted) throw abortError()
      if (!isTransientPollError(err)) throw err
      failures += 1
      if (failures >= JOB_MAX_POLL_FAILURES) {
        throw new JobStillRunningError(
          "Lost contact with the server while this was running. It may still be working, so check the result before trying again.",
          jobId,
        )
      }
    }
    if (job) {
      if (job.status === "done") return JSON.parse(job.result ?? "null") as T
      if (job.status === "failed") throw new Error(job.result || "The operation failed")
    }
    if (Date.now() > deadline) {
      throw new JobStillRunningError(
        "This is taking longer than expected. The server may still be working on it, so check the result before trying again.",
        jobId,
      )
    }
  }
}

// Starts a long server-side operation as a job and resolves with the same body the synchronous endpoint
// returns. Validation and permission errors still reject immediately (they answer before the job exists);
// a job that fails rejects with its message.
async function postJob<T>(path: string, body: unknown, opts: { signal?: AbortSignal } = {}): Promise<T> {
  const generation = jobGeneration()
  const { job_id } = await post<{ job_id: number }>(`${path}?async=1`, body)
  // Remember the job so it can be found again after a reload or navigation (see lib/running-jobs.ts), unless
  // the identity was reset (sign-out) while the request was in flight.
  if (generation === jobGeneration()) trackJob({ jobId: job_id, ...describeJob(path, body), startedAt: Date.now() })
  markLive(job_id)
  try {
    const result = await awaitJob<T>(job_id, opts)
    forgetJob(job_id)
    return result
  } catch (err) {
    // A job that ended (failed) or that the server does not know is finished business; one that may still be
    // running (we lost contact, or ran out of patience) stays tracked so it can be re-attached.
    if (!(err instanceof JobStillRunningError) && !(err instanceof DOMException && err.name === "AbortError")) {
      forgetJob(job_id)
    }
    throw err
  } finally {
    unmarkLive(job_id)
  }
}

// Carries an optional client-supplied PAT to GET endpoints via a header (never
// a query string, which would leak into logs/browser history).
function githubTokenHeader(token?: string): Record<string, string> | undefined {
  return token ? { "X-GitHub-Token": token } : undefined
}

async function get<T>(path: string, extraHeaders?: Record<string, string>): Promise<T> {
  const res = await fetchWithTimeout(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...getAuthHeaders(), ...extraHeaders },
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
  // Same path as every other verb, so a failed delete throws an ApiError that carries `status`.
  await handleResponse<unknown>(res)
}


export function normalizeCheckValue(id: string, raw: unknown): CheckValue {
  const value = normalizeCheckValueShape(id, raw)
  // The checks record the offending repos beside their counts; keep them for the card to list.
  const repos = (raw as { repos?: unknown } | null)?.repos
  if ((value?.type === "ratio" || value?.type === "severity_counts") && Array.isArray(repos)) {
    const names = repos.filter((r): r is string => typeof r === "string")
    if (names.length > 0) return { ...value, repos: names }
  }
  return value
}

function normalizeCheckValueShape(id: string, raw: unknown): CheckValue {
  // Checked first: an error explanation must not be coerced (Boolean("Check failed…") is true).
  if (typeof raw === "string") return raw ? { type: "text", text: raw } : null
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
    if ("checked" in r && "missing" in r) {
      // Hygiene checks: repos that have the thing / repos evaluated.
      return { type: "ratio", numerator: Number(r.checked) - Number(r.missing), denominator: Number(r.checked) }
    }
    if ("checked" in r && "protected" in r) {
      return {
        type: "ratio",
        numerator: Number(r.protected),
        denominator: Number(r.checked),
        ...(Number(r.unknown) > 0 && { unknown: Number(r.unknown) }),
      }
    }
    if ("enabled" in r && "total" in r) {
      return {
        type: "ratio",
        numerator: Number(r.enabled),
        denominator: Number(r.total),
        ...(Number(r.unknown) > 0 && { unknown: Number(r.unknown) }),
      }
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


export const api = {
  compliance: {
    // Static check-to-control table (SOC 2, CIS, ISO 27001) for the compliance matrix.
    controls: () => get<ControlMapping>("/compliance/controls"),
  },
  notifications: {
    // `days` widens the window past the bell's default (14 days); `before` (the `at` of the last item
    // already loaded, with `before_id` its id so items sharing that time are not skipped) pages to older items. The server then reports `unread_count` 0.
    feed: (org: string, opts: { days?: number; limit?: number; before?: string; before_id?: string } = {}) => {
      const params = new URLSearchParams()
      if (opts.days) params.set("days", String(opts.days))
      if (opts.limit) params.set("limit", String(opts.limit))
      if (opts.before) params.set("before", opts.before)
      if (opts.before && opts.before_id) params.set("before_id", opts.before_id)
      const qs = params.toString()
      return get<NotificationFeed>(`/orgs/${encodeURIComponent(org)}/notifications${qs ? `?${qs}` : ""}`)
    },
    // 204: marks one item (an id from the feed) read for the caller, without moving the read-up-to marker.
    dismiss: (org: string, itemId: string) =>
      post<null>(`/orgs/${encodeURIComponent(org)}/notifications/${encodeURIComponent(itemId)}/dismiss`, {}),
    // 204: marks everything currently in the feed as read for the caller.
    // `upTo` = the newest item shown, so an item that arrived after the feed loaded stays unread.
    markRead: (org: string, upTo?: string) =>
      post<null>(`/orgs/${encodeURIComponent(org)}/notifications/read`, upTo ? { up_to: upTo } : {}),
    // 204: marks every unread item of one kind read for the caller (per-item marks, read-up-to marker untouched).
    markKindRead: (org: string, kind: NotificationKind) =>
      post<null>(`/orgs/${encodeURIComponent(org)}/notifications/read-kind`, { kind }),
    // The caller's own muted kinds for this org; the server drops them from the feed and unread count.
    preferences: (org: string) =>
      get<NotificationPreferences>(`/orgs/${encodeURIComponent(org)}/notifications/preferences`),
    setPreferences: (org: string, muted_kinds: NotificationPreferences["muted_kinds"]) =>
      put<NotificationPreferences>(`/orgs/${encodeURIComponent(org)}/notifications/preferences`, { muted_kinds }),
  },
  analytics: {
    // The org's newest stored scan with every check (404 = never scanned), so results show without a rescan.
    latestScan: async (orgLogin: string): Promise<LatestScanResponse> => {
      const data = await get<LatestScanResponse>(`/orgs/${encodeURIComponent(orgLogin)}/analytics/latest-scan`)
      return { ...data, checks: data.checks.map((c) => ({ ...c, value: normalizeCheckValue(c.id, c.value) })) }
    },
    // The org's latest scan compared with the one before it, check by check.
    changes: (orgLogin: string) => get<ScanChanges>(`/orgs/${encodeURIComponent(orgLogin)}/analytics/changes`),
    // token is optional — the API falls back to a connected GitHub App installation.
    overview: async (owner: string, token: string): Promise<AnalyticsOverviewResponse> => {
      const data = await postJob<AnalyticsOverviewResponse>("/me/analytics/overview", {
        owner,
        token: token || undefined,
      })
      return {
        ...data,
        checks: data.checks.map((c) => ({ ...c, value: normalizeCheckValue(c.id, c.value) })),
      }
    },
    // The API answers 403 when the caller has no scans or installation for this owner -- the normal
    // never-scanned state, not a failure -- so that reads as an empty history.
    history: (owner: string) =>
      get<AnalyticsHistoryResponse>(`/me/analytics/history?owner=${encodeURIComponent(owner)}`).catch(
        (err: unknown) => {
          if (err instanceof ApiError && err.status === 403) return []
          throw err
        },
      ),
    // Admin-only; needs an App permission not requested by default, so a missing-permission
    // 403 comes back as a 400 (the Overview card hides itself on error).
    actionsUsage: (org: string, token?: string) =>
      get<ActionsUsageResponse>(
        `/orgs/${encodeURIComponent(org)}/usage/actions`,
        githubTokenHeader(token),
      ),
    // The org's recorded daily Actions cache totals (a point is written each time the Overview loads).
    cacheHistory: (org: string, days = 30) =>
      get<CacheSizePoint[]>(`/orgs/${encodeURIComponent(org)}/analytics/cache-history?days=${days}`),
    // Full scan history with per-check detail for an optional [since, until] window; caller renders CSV.
    exportHistory: (owner: string, since?: string, until?: string) => {
      const params = new URLSearchParams({ owner })
      if (since) params.set("since", since)
      if (until) params.set("until", until)
      return get<ScanExportResponse>(`/me/analytics/export?${params.toString()}`)
    },
    // token is optional (App-or-PAT fallback), sent via header since this is a GET.
    cockpit: (owner: string, token?: string) =>
      get<CockpitResponse>(`/me/analytics/cockpit/${encodeURIComponent(owner)}`, githubTokenHeader(token)),
    myView: (owner: string, token?: string) =>
      get<MyViewResponse>(`/me/github/my-view?owner=${encodeURIComponent(owner)}`, githubTokenHeader(token)),
    myPrs: (owner: string, page = 1, perPage = 25, token?: string) =>
      get<MyPrListResponse>(
        `/me/github/my-prs?owner=${encodeURIComponent(owner)}&page=${page}&per_page=${perPage}`,
        githubTokenHeader(token),
      ),
    myReviews: (owner: string, page = 1, perPage = 25, token?: string) =>
      get<MyPrListResponse>(
        `/me/github/my-reviews?owner=${encodeURIComponent(owner)}&page=${page}&per_page=${perPage}`,
        githubTokenHeader(token),
      ),
    myIssues: (owner: string, page = 1, perPage = 25, token?: string) =>
      get<MyIssueListResponse>(
        `/me/github/my-issues?owner=${encodeURIComponent(owner)}&page=${page}&per_page=${perPage}`,
        githubTokenHeader(token),
      ),
  },
  security: {
    /** Open alerts by severity against the SLA; `kind` picks Dependabot (default), code scanning or both. */
    dependabotBurndown: (orgLogin: string, days = 30, kind: "dependabot" | "code_scanning" | "all" = "dependabot") =>
      get<DependabotBurndown>(
        `/orgs/${encodeURIComponent(orgLogin)}/security/dependabot-burndown?days=${days}&kind=${kind}`,
      ),
    matrix: (owner: string, token?: string) =>
      get<SecurityMatrixResponse>(
        `/me/analytics/security-matrix/${encodeURIComponent(owner)}`,
        githubTokenHeader(token),
      ),
    // The org matrix's row for one repo, without scanning the rest of the org.
    repoRow: (owner: string, repo: string, token?: string) =>
      get<RepoSecurityRow>(
        `/me/analytics/security-matrix/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
        githubTokenHeader(token),
      ),
    secretScanning: (owner: string, repo: string, token?: string) =>
      get<SecretScanningResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/secret-scanning`,
        githubTokenHeader(token),
      ),
    // Org-admin only; needs `Administration: write`. dry_run (the default server-side) previews what
    // each repo would get and writes nothing; a 400 with a docs pointer means the App lacks the permission.
    remediateBulk: (
      org: string,
      body: { check_id: string; repos: string[]; dry_run: boolean; token?: string },
    ) =>
      postJob<BulkRemediateResponse>(`/orgs/${encodeURIComponent(org)}/security/remediate/bulk`, {
        ...body,
        token: body.token || undefined,
      }),
    // Needs a write-scoped token; a GitHub 403 comes back as a 400 with a permission hint.
    // Admin-gated when `owner` is a connected Clevis org.
    remediate: (owner: string, repo: string, checkId: string, token?: string) =>
      post<{ check_id: string; repo: string; remediated: boolean }>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/security/checks/${encodeURIComponent(checkId)}/remediate`,
        { token: token || undefined },
      ),
  },
  issues: {
    // Needs `Issues: write`; a GitHub 403 surfaces as a 400. Admin-gated when `owner` is a connected Clevis org.
    create: (owner: string, repo: string, body: { title: string; body: string }, token?: string) =>
      post<CreateIssueResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues`,
        { ...body, token: token || undefined },
      ),
  },
  prNudges: {
    // Needs `Pull requests: write`; a GitHub 403 surfaces as a 400. Org-admin gated.
    // `dryRun` previews which PRs a real run would comment on or label, without posting anything.
    // `onlyPrs` limits a real run to those PR numbers (the ones a preview showed).
    sweep: (org: string, owner: string, repo: string, token?: string, dryRun?: boolean, onlyPrs?: number[]) =>
      post<PrNudgeResponse>(
        `/orgs/${encodeURIComponent(org)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pr-nudges`,
        { token: token || undefined, ...(dryRun ? { dry_run: true } : {}), ...(onlyPrs ? { only_prs: onlyPrs } : {}) },
      ),
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
      body: { token: string; dry_run: boolean; key?: string; ref?: string },
    ) =>
      post<CacheClearResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions-caches/clear`,
        { ...body, token: body.token || undefined },
      ),
  },
  repos: {
    // /me/repos/{owner} works for an org the caller belongs to and for a personal account, which has no
    // Clevis org row for /orgs/{org}/repos to resolve.
    list: (owner: string, token: string) =>
      post<RepoListResponse>(`/me/repos/${encodeURIComponent(owner)}`, { token: token || undefined }),
    // The per-repo routes are /me too, for the same reason: the org-scoped ones 404 for a personal account.
    stats: (owner: string, repo: string, token: string) =>
      post<RepoStatsResponse>(`/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/stats`, { token: token || undefined }),
    flowMetrics: (owner: string, repo: string, token: string) =>
      post<RepoFlowMetricsResponse>(`/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/flow-metrics`, { token: token || undefined }),
    pulls: (owner: string, repo: string, token: string) =>
      post<RepoPullsResponse>(`/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pulls`, { token: token || undefined }),
    security: (owner: string, repo: string, token: string) =>
      post<RepoSecurityResponse>(`/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/security`, { token: token || undefined }),
  },
  jobs: {
    list: () => get<JobOut[]>("/jobs"),
    // One org's own jobs, newest first (no payload). Org-admin only; pages with `before_id`.
    listForOrg: (org: string, params: { before_id?: number; limit?: number } = {}) => {
      const qs = new URLSearchParams()
      if (params.before_id !== undefined) qs.set("before_id", String(params.before_id))
      if (params.limit !== undefined) qs.set("limit", String(params.limit))
      const suffix = qs.toString()
      return get<JobOut[]>(`/orgs/${encodeURIComponent(org)}/jobs${suffix ? `?${suffix}` : ""}`)
    },
    get: (jobId: number) => get<JobOut>(`/jobs/${jobId}`),
  },
  automation: {
    // token is optional (App-or-PAT fallback), sent via header since these are GETs.
    workflows: (owner: string, repo: string, token?: string) =>
      get<WorkflowsResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/workflows`,
        githubTokenHeader(token),
      ),
    runs: (owner: string, repo: string, token?: string, perPage = 10) =>
      get<RunsResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/runs?per_page=${perPage}`,
        githubTokenHeader(token),
      ),
    dispatch: (
      owner: string,
      repo: string,
      workflowId: number,
      body: { token: string; ref: string; inputs?: Record<string, string> },
    ) =>
      post<DispatchResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/workflows/${workflowId}/dispatch`,
        { ...body, token: body.token || undefined },
      ),
    dispatchAll: (owner: string, repo: string, body: { token: string; ref: string }) =>
      post<DispatchAllResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/workflows/dispatch-all`,
        { ...body, token: body.token || undefined },
      ),
  },
  branchProtection: {
    // Most recently saved preset (flattened knobs), or null. Org-admin only.
    savedPreset: (org: string) =>
      get<{ preset: SavedBranchProtectionPreset | null }>(`/orgs/${encodeURIComponent(org)}/branch-protection/preset`),
    // Org-admin only; needs `Administration: write`. dry_run returns a per-repo diff and writes nothing.
    // A 400 with a docs pointer means the App is missing the permission.
    bulk: (
      org: string,
      body: {
        repos: string[]
        preset?: BranchProtectionPreset
        dry_run: boolean
        save_preset?: boolean
        token?: string
      },
    ) =>
      postJob<BranchProtectionBulkResponse>(`/orgs/${encodeURIComponent(org)}/branch-protection/bulk`, {
        ...body,
        token: body.token || undefined,
      }),
  },
  workflowLint: {
    // A scan needs only membership (or a PAT); open_pr needs org-admin for a connected org and
    // returns the fix PR URL. A 400 with a docs pointer means the App is missing a write scope.
    scan: (owner: string, repo: string, body: { open_pr: boolean }, token?: string) =>
      post<WorkflowLintResponse>(
        `/me/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/workflow-lint`,
        { ...body, token: token || undefined },
      ),
  },
  dependabotTriage: {
    // Default off; only patch-level dependabot[bot] bumps with green checks and no pending human
    // review are acted on. approve_and_merge is the only mode that merges.
    getRepo: (org: string, owner: string, repo: string) =>
      get<{ enabled: boolean; mode: "approve_only" | "approve_and_merge"; merge_method: string }>(
        `/orgs/${encodeURIComponent(org)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/automation/dependabot-triage`,
      ),
    setRepo: (
      org: string,
      owner: string,
      repo: string,
      body: { enabled: boolean; mode?: "approve_only" | "approve_and_merge"; merge_method?: string },
    ) =>
      put<{ enabled: boolean; mode: string; merge_method: string }>(
        `/orgs/${encodeURIComponent(org)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/automation/dependabot-triage`,
        body,
      ),
    run: (org: string, body: { repos?: string[]; dry_run: boolean }, token?: string) =>
      postJob<DependabotTriageResponse>(`/orgs/${encodeURIComponent(org)}/dependabot-triage`, {
        ...body,
        token: token || undefined,
      }),
  },
  github: {
    events: (org: string, token: string, perPage = 30) =>
      post<OrgEventsResponse>(`/github/orgs/${encodeURIComponent(org)}/events`, {
        token: token || undefined,
        per_page: perPage,
      }),
    // Rollup of webhook-ingested events: no GitHub call, no token, so it is cheap to poll or stream.
    activitySummary: (org: string, days = 7) =>
      get<ActivitySummary>(`/github/orgs/${encodeURIComponent(org)}/activity-summary?days=${days}`),
    failedRuns: (org: string, token: string, limit = 20) =>
      post<FailedRunsResponse>(`/github/orgs/${encodeURIComponent(org)}/failed-runs`, {
        token: token || undefined,
        limit,
      }),
    releaseTimeline: (org: string, token: string, days = 90) =>
      post<ReleaseTimelineResponse>(`/github/orgs/${encodeURIComponent(org)}/release-timeline`, {
        token: token || undefined,
        days,
      }),
  },
  audit: {
    // The instance-wide log, newest first. Workspace-admin only. Pages with `before_id` (the id of the
    // last row already loaded); `since`/`until` are ISO timestamps.
    list: (filters: AdminAuditFilters = {}) => {
      const params = new URLSearchParams()
      for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined && value !== "") params.set(key, String(value))
      }
      const qs = params.toString()
      return get<AuditLogOut[]>(`/audit${qs ? `?${qs}` : ""}`)
    },
    // One org's own audit rows, newest first. Org-admin only. Pages with `before_id` (the id of the
    // last row already loaded); `since`/`until` are ISO timestamps.
    listForOrg: (org: string, filters: OrgAuditFilters = {}) => {
      const params = new URLSearchParams()
      for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined && value !== "") params.set(key, String(value))
      }
      const qs = params.toString()
      return get<AuditLogOut[]>(`/orgs/${encodeURIComponent(org)}/audit${qs ? `?${qs}` : ""}`)
    },
    // Distinct action names actually present in the log, for the filter dropdown.
    actions: () => get<string[]>("/audit/actions"),
    // Every audit row matching the filters (not just the loaded pages) as a CSV, up to the server's cap;
    // `truncated` is true when the cap cut it. Org-admin only.
    exportForOrg: (org: string, filters: OrgAuditFilters = {}) => {
      const qs = new URLSearchParams()
      for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined && value !== "") qs.set(key, String(value))
      }
      return downloadFile(`/orgs/${encodeURIComponent(org)}/audit/export`, qs, {
        fallbackName: `clevis-audit-${org}.csv`,
        truncatedHeader: "X-Audit-Truncated",
        timeoutMessage: "The audit export timed out — try a shorter date range.",
      })
    },
  },
  installations: {
    list: () => get<InstallationMeta[]>("/me/installations"),
    // Organization installations GitHub announced that nobody has connected yet, where you approved or asked for it.
    pending: () => get<PendingInstallation[]>("/me/pending-installations"),
    // Org-connected installation (list() only returns personal ones). 404 = org not connected,
    // 403 = not a member -- callers should treat either as "not installed".
    listForOrg: (orgLogin: string) => get<InstallationMeta[]>(`/orgs/${encodeURIComponent(orgLogin)}/installations`),
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
    // Re-reads the installation's granted permissions from GitHub and returns the updated row.
    refreshPermissions: (
      target: { scope: "me" } | { scope: "org"; orgLogin: string },
      installationId: number,
    ) =>
      target.scope === "me"
        ? post<InstallationMeta>(`/me/installations/${installationId}/refresh-permissions`, {})
        : post<InstallationMeta>(
            `/orgs/${encodeURIComponent(target.orgLogin)}/installations/${installationId}/refresh-permissions`,
            {},
          ),
    // Uninstalls the App on GitHub's side (a real revocation), then removes the local row.
    remove: (
      target: { scope: "me" } | { scope: "org"; orgLogin: string },
      installationId: number,
    ) =>
      target.scope === "me"
        ? del(`/me/installations/${installationId}`)
        : del(`/orgs/${encodeURIComponent(target.orgLogin)}/installations/${installationId}`),
  },
  evidence: {
    // The compliance evidence zip (admin only). Built server-side, so it can take longer than the
    // default request timeout; `truncated` is true when any list hit its cap (the zip's manifest says which).
    download: (org: string, range: { since?: string; until?: string } = {}) => {
      const qs = new URLSearchParams()
      if (range.since) qs.set("since", range.since)
      if (range.until) qs.set("until", range.until)
      return downloadFile(`/orgs/${encodeURIComponent(org)}/evidence`, qs, {
        fallbackName: `clevis-evidence-${org}.zip`,
        truncatedHeader: "X-Evidence-Truncated",
        timeoutMessage: "The evidence export timed out — try a shorter date range.",
      })
    },
  },
  waivers: {
    // The checks the API accepts a waiver for (and which are org-wide only); the form reads this, not a copy.
    checks: (orgLogin: string) => get<WaivableCheck[]>(`/orgs/${encodeURIComponent(orgLogin)}/waivable-checks`),
    list: (orgLogin: string) => get<CheckWaiver[]>(`/orgs/${encodeURIComponent(orgLogin)}/waivers`),
    create: (orgLogin: string, body: CheckWaiverInput) =>
      post<CheckWaiver>(`/orgs/${encodeURIComponent(orgLogin)}/waivers`, body),
    // Renew or edit: PUT replaces the waiver's check, repos, reason, owner and expiry.
    update: (orgLogin: string, id: number, body: CheckWaiverInput) =>
      put<CheckWaiver>(`/orgs/${encodeURIComponent(orgLogin)}/waivers/${id}`, body),
    revoke: (orgLogin: string, id: number) => del(`/orgs/${encodeURIComponent(orgLogin)}/waivers/${id}`),
  },
  // A member asks the org admins for a waiver; an admin approves (which creates it) or denies.
  waiverRequests: {
    list: (orgLogin: string, status?: WaiverRequest["status"]) =>
      get<WaiverRequest[]>(
        `/orgs/${encodeURIComponent(orgLogin)}/waiver-requests${status ? `?status=${status}` : ""}`,
      ),
    create: (orgLogin: string, body: WaiverRequestInput) =>
      post<WaiverRequest>(`/orgs/${encodeURIComponent(orgLogin)}/waiver-requests`, body),
    approve: (orgLogin: string, id: number, body: { owner?: string; expires_at?: string } = {}) =>
      post<WaiverRequest>(`/orgs/${encodeURIComponent(orgLogin)}/waiver-requests/${id}/approve`, body),
    deny: (orgLogin: string, id: number, note?: string) =>
      post<WaiverRequest>(`/orgs/${encodeURIComponent(orgLogin)}/waiver-requests/${id}/deny`, { note: note || null }),
  },
  notificationDestinations: {
    list: (orgLogin: string) =>
      get<NotificationDestination[]>(`/orgs/${encodeURIComponent(orgLogin)}/notification-destinations`),
    create: (orgLogin: string, body: NotificationDestinationInput) =>
      post<NotificationDestination>(`/orgs/${encodeURIComponent(orgLogin)}/notification-destinations`, body),
    update: (orgLogin: string, id: number, body: NotificationDestinationPatch) =>
      patch<NotificationDestination>(`/orgs/${encodeURIComponent(orgLogin)}/notification-destinations/${id}`, body),
    remove: (orgLogin: string, id: number) =>
      del(`/orgs/${encodeURIComponent(orgLogin)}/notification-destinations/${id}`),
    test: (orgLogin: string, id: number) =>
      post<{ ok: boolean; detail: string }>(
        `/orgs/${encodeURIComponent(orgLogin)}/notification-destinations/${id}/test`,
        {},
      ),
    // Sends the currently open critical/high alerts once; stamps nothing.
    announceOpenAlerts: (orgLogin: string) =>
      post<{ announced: number; truncated: boolean }>(
        `/orgs/${encodeURIComponent(orgLogin)}/notification-destinations/announce-open-alerts`,
        {},
      ),
  },
  apiTokens: {
    list: (orgLogin: string) => get<ApiToken[]>(`/orgs/${encodeURIComponent(orgLogin)}/api-tokens`),
    // expiresInDays: omit for a token that never expires.
    create: (orgLogin: string, name: string, expiresInDays?: number) =>
      post<ApiTokenCreated>(`/orgs/${encodeURIComponent(orgLogin)}/api-tokens`, {
        name,
        ...(expiresInDays ? { expires_in_days: expiresInDays } : {}),
      }),
    revoke: (orgLogin: string, id: number) => del(`/orgs/${encodeURIComponent(orgLogin)}/api-tokens/${id}`),
    policy: (orgLogin: string) =>
      get<{ revoke_on_creator_leave: boolean }>(`/orgs/${encodeURIComponent(orgLogin)}/api-token-policy`),
    setPolicy: (orgLogin: string, revokeOnCreatorLeave: boolean) =>
      put<{ revoke_on_creator_leave: boolean }>(`/orgs/${encodeURIComponent(orgLogin)}/api-token-policy`, {
        revoke_on_creator_leave: revokeOnCreatorLeave,
      }),
  },
  orgs: {
    mine: () => get<MyOrgMembership[]>("/me/orgs"),
    hygieneScoring: (orgLogin: string) =>
      get<HygieneScoringSettings>(`/orgs/${encodeURIComponent(orgLogin)}/hygiene-scoring`),
    // enabled: null clears the org's override so it follows the instance setting again.
    setHygieneScoring: (orgLogin: string, enabled: boolean | null) =>
      put<HygieneScoringSettings>(`/orgs/${encodeURIComponent(orgLogin)}/hygiene-scoring`, { enabled }),
    scoringExclusions: (orgLogin: string) =>
      get<ScoringExclusions>(`/orgs/${encodeURIComponent(orgLogin)}/scoring-exclusions`),
    setScoringExclusions: (orgLogin: string, patterns: string[]) =>
      put<ScoringExclusions>(`/orgs/${encodeURIComponent(orgLogin)}/scoring-exclusions`, { patterns }),
    scheduledScans: (orgLogin: string) =>
      get<ScheduledScanSettings>(`/orgs/${encodeURIComponent(orgLogin)}/scheduled-scans`),
    // enabled: null clears the org's override so it follows the instance cadence again.
    setScheduledScans: (orgLogin: string, enabled: boolean | null) =>
      put<ScheduledScanSettings>(`/orgs/${encodeURIComponent(orgLogin)}/scheduled-scans`, { enabled }),
    automationSchedules: (orgLogin: string) =>
      get<AutomationSchedules>(`/orgs/${encodeURIComponent(orgLogin)}/automation-schedules`),
    // Only the fields passed change; null follows the instance cadence again.
    setAutomationSchedules: (
      orgLogin: string,
      body: { pr_nudges?: boolean | null; dependabot_triage?: boolean | null; stale_cache_clear?: boolean | null },
    ) =>
      put<AutomationSchedules>(`/orgs/${encodeURIComponent(orgLogin)}/automation-schedules`, body),
    setStaleCacheRepo: (orgLogin: string, owner: string, repo: string, enabled: boolean, idleDays: number) =>
      put<{ enabled: boolean; idle_days: number }>(
        `/orgs/${encodeURIComponent(orgLogin)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/automation/stale-cache-clear`,
        { enabled, idle_days: idleDays },
      ),
    setPrNudgeRepo: (orgLogin: string, owner: string, repo: string, enabled: boolean) =>
      put<{ enabled: boolean }>(
        `/orgs/${encodeURIComponent(orgLogin)}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/automation/pr-nudge`,
        { enabled },
      ),
    digest: (orgLogin: string) => get<DigestSettings>(`/orgs/${encodeURIComponent(orgLogin)}/digest`),
    // Only the fields passed change: enabled null follows the instance, recipients null means every verified admin.
    setDigest: (orgLogin: string, body: { enabled?: boolean | null; recipients?: string[] | null }) =>
      put<DigestSettings>(`/orgs/${encodeURIComponent(orgLogin)}/digest`, body),
    digestPreview: (orgLogin: string) => get<DigestPreview>(`/orgs/${encodeURIComponent(orgLogin)}/digest/preview`),
    sendTestDigest: (orgLogin: string) => post<{ sent_to: string }>(`/orgs/${encodeURIComponent(orgLogin)}/digest/test`, {}),
    badge: (orgLogin: string) => get<{ enabled: boolean }>(`/orgs/${encodeURIComponent(orgLogin)}/badge`),
    setBadge: (orgLogin: string, enabled: boolean) =>
      put<{ enabled: boolean }>(`/orgs/${encodeURIComponent(orgLogin)}/badge`, { enabled }),
  },
  orgMemberships: {
    list: (orgLogin: string) => get<OrgMembership[]>(`/orgs/${encodeURIComponent(orgLogin)}/members`),
    setRole: (orgLogin: string, userId: number, role: "member" | "admin") =>
      put<OrgMembership>(`/orgs/${encodeURIComponent(orgLogin)}/members/${userId}`, { role }),
    remove: (orgLogin: string, userId: number) => del(`/orgs/${encodeURIComponent(orgLogin)}/members/${userId}`),
    /** Leave an organization yourself (invite-sourced memberships only). */
    leave: (orgLogin: string) => del(`/orgs/${encodeURIComponent(orgLogin)}/members/me`),
  },
  ingestionHealth: (orgLogin: string) => get<IngestionHealth>(`/orgs/${encodeURIComponent(orgLogin)}/ingestion-health`),
  invitations: {
    // The caller's own pending invitations (empty until their email is verified).
    mine: () => get<MyInvitation[]>(`/invitations/mine`),
    // sendEmail also emails the link (the server needs SMTP, else it answers 409 and creates nothing).
    create: (orgLogin: string, email: string, sendEmail = false) =>
      post<InvitationCreateResponse>(`/orgs/${encodeURIComponent(orgLogin)}/invitations`, {
        email,
        ...(sendEmail ? { send_email: true } : {}),
      }),
    createMany: (orgLogin: string, emails: string[], sendEmail = false) =>
      post<InvitationBulkResponse>(`/orgs/${encodeURIComponent(orgLogin)}/invitations/bulk`, {
        emails,
        ...(sendEmail ? { send_email: true } : {}),
      }),
    list: (orgLogin: string) => get<InvitationOut[]>(`/orgs/${encodeURIComponent(orgLogin)}/invitations`),
    regenerate: (orgLogin: string, invitationId: number) =>
      post<InvitationCreateResponse>(`/orgs/${encodeURIComponent(orgLogin)}/invitations/${invitationId}/regenerate`, {}),
    resend: (orgLogin: string, invitationId: number) =>
      post<InvitationOut>(`/orgs/${encodeURIComponent(orgLogin)}/invitations/${invitationId}/resend`, {}),
    revoke: (orgLogin: string, invitationId: number) =>
      post<InvitationOut>(`/orgs/${encodeURIComponent(orgLogin)}/invitations/${invitationId}/revoke`, {}),
    preview: (token: string) => get<InvitationPreview>(`/invitations/${encodeURIComponent(token)}`),
    accept: (token: string) => post<{ org_login: string; role: string }>(`/invitations/${encodeURIComponent(token)}/accept`, {}),
  },
  collab: {
    // token is optional — only orgs without a connected App installation need it.
    members: (orgLogin: string, role: "all" | "member" | "admin" = "all", token?: string) =>
      get<GithubOrgMembersResponse>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/members?role=${role}`,
        githubTokenHeader(token),
      ),
    outsideCollaborators: (orgLogin: string, token?: string) =>
      get<GithubOutsideCollaboratorsResponse>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/outside_collaborators`,
        githubTokenHeader(token),
      ),
    invitations: (orgLogin: string, token?: string) =>
      get<GithubOrgInvitationsResponse>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/invitations`,
        githubTokenHeader(token),
      ),
    membership: (orgLogin: string, username: string, token?: string) =>
      get<GithubMembershipStatus>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/members/${encodeURIComponent(username)}/membership`,
        githubTokenHeader(token),
      ),
    // Admin only; read from ingested data, so no GitHub token is involved.
    memberAccess: (orgLogin: string, username: string) =>
      get<MemberAccess>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/members/${encodeURIComponent(username)}/access`,
      ),
    permissionAudit: (orgLogin: string, token?: string) =>
      get<PermissionAuditResponse>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/permission-audit`,
        githubTokenHeader(token),
      ),
    inactiveMembers: (orgLogin: string, days = 30, token?: string) =>
      get<InactiveMembersResponse>(
        `/github/orgs/${encodeURIComponent(orgLogin)}/inactive-members?days=${days}`,
        githubTokenHeader(token),
      ),
  },
  tokens: {
    list: () => get<SavedTokenMeta[]>("/tokens"),
    upsert: (org: string, token: string, label?: string) =>
      put<SavedTokenMeta>(`/tokens/${encodeURIComponent(org)}`, { token, label }),
    resolve: (org: string) =>
      post<{ token: string }>("/tokens/resolve", { org }),
    delete: (org: string) => del(`/tokens/${encodeURIComponent(org)}`),
  },
  githubApp: {
    status: () => get<GithubAppStatus>("/github-app"),
    manifest: (body: { ui_origin: string; organization?: string; read_only?: boolean; name?: string; replace?: boolean }) =>
      post<GithubAppManifest>("/github-app/manifest", body),
    convert: (body: { code: string; state: string }) =>
      post<{ slug: string; app_id: string }>("/github-app/manifest/convert", body),
  },
  config: {
    getAll: () => get<Record<string, string>>("/config"),
    update: (key: string, value: string) =>
      put<Record<string, string>>(`/config/${encodeURIComponent(key)}`, { value }),
  },
  auth: {
    setupRequired: () =>
      get<{
        setup_required: boolean
        registration_enabled?: boolean
        github_oauth_enabled?: boolean
        github_app_configured?: boolean
        github_app_slug?: string | null
      }>("/auth/setup-required"),
    setup: (email: string, password: string, name?: string) =>
      post<{ access_token: string; user: { id: number; email: string; name: string | null; is_workspace_admin: boolean } }>(
        "/auth/setup",
        { email, password, name },
      ),
    register: (email: string, password: string, name?: string, inviteToken?: string) =>
      post<{
        access_token: string
        user: { id: number; email: string; name: string | null; is_workspace_admin: boolean }
        pending_invitations: PendingInvitationSummary[]
      }>("/auth/register", { email, password, name, invite_token: inviteToken }),
    me: () =>
      get<{
        id: number
        email: string
        name: string | null
        is_workspace_admin: boolean
        email_verified: boolean
        /** The linked GitHub account, once there is one. */
        github_login?: string | null
      }>("/auth/me"),
    /** Start linking a GitHub account to the signed-in user; resolves with the GitHub authorize URL to open. */
    linkGithub: () => post<{ url: string }>("/auth/github/link", {}),
    patchMe: (name: string) =>
      patch<{ id: number; email: string; name: string | null; is_workspace_admin: boolean }>("/auth/me", { name }),
    revokeSessions: () => post<{ ok: boolean }>("/auth/me/revoke-sessions", {}),
    // Sessions Clevis has a record of, most recently used first.
    sessions: () => get<UserSessionOut[]>("/auth/me/sessions"),
    // Ends one other session; 204. The current session is refused (use logout).
    revokeSession: (id: string) => del(`/auth/me/sessions/${encodeURIComponent(id)}`),
    /** Also sets the account password and ends every earlier session, so a squatter's password stops working. */
    verifyEmail: (token: string, password: string) => post<{ ok: boolean }>("/auth/verify-email", { token, password }),
    /** Workspace admin only: mark an account's email verified without the emailed link (for no-SMTP deployments). */
    adminVerifyEmail: (email: string) =>
      post<{ ok: boolean; already_verified: boolean }>("/auth/admin/verify-email", { email }),
    requestEmailChange: (newEmail: string, password: string) =>
      post<{ ok: boolean }>("/auth/me/email", { new_email: newEmail, password }),
    confirmEmailChange: (token: string) => post<{ ok: boolean }>("/auth/confirm-email-change", { token }),
    deleteAccount: (confirmEmail: string, password?: string) =>
      post<void>("/auth/me/delete", { confirm_email: confirmEmail, password }),
    forgotPassword: (email: string) => post<{ ok: boolean }>("/auth/forgot-password", { email }),
    resetPassword: (token: string, password: string) => post<{ ok: boolean }>("/auth/reset-password", { token, password }),
    changePassword: (currentPassword: string, newPassword: string) =>
      post<{
        access_token: string
        user: { id: number; email: string; name: string | null; is_workspace_admin: boolean }
        pending_invitations: PendingInvitationSummary[]
      }>("/auth/me/password", { current_password: currentPassword, new_password: newPassword }),
    resendVerification: () =>
      post<{ ok: boolean; already_verified: boolean }>("/auth/resend-verification", {}),
  },
}
