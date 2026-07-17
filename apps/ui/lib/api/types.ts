export interface MFACheckValue {
  type: "boolean"
  enabled: boolean
}

export interface RatioCheckValue {
  type: "ratio"
  numerator: number
  denominator: number
}

export type CheckValue = MFACheckValue | RatioCheckValue | null

export interface CheckResult {
  id: string
  title: string
  severity: "high" | "medium" | "low"
  remediation: string
  status: "pass" | "fail" | "error" | "not_applicable"
  value: CheckValue
}

export interface AnalyticsOverviewResponse {
  owner: string
  score: number
  total_checks: number
  failed_checks: number
  repo_count: number
  checks: CheckResult[]
}

export interface CacheEntry {
  id: number
  ref: string
  key: string
  version: string
  size_in_bytes: number
  created_at: string
  last_accessed_at: string
}

export interface CacheListResponse {
  repository: string
  total: number
  actions_caches: CacheEntry[]
}

export interface CacheClearResponse {
  queued: boolean
  dry_run?: boolean | null
  job_id?: number | null
  message?: string | null
}

export interface RepoSummary {
  name: string
  full_name: string
  private: boolean
  description: string | null
  language: string | null
  stargazers_count: number
  forks_count: number
  watchers_count: number
  open_issues_count: number
  pushed_at: string | null
  default_branch: string
  html_url: string
}

export interface RepoListResponse {
  org: string
  total: number
  repos: RepoSummary[]
}

export interface CommitActivityWeek {
  week: number
  total: number
  days: number[]
}

export interface LatestRelease {
  tag_name: string
  published_at: string | null
  html_url: string
}

export interface RepoStatsResponse {
  repository: string
  commit_activity: CommitActivityWeek[]
  participation: { all?: number[]; owner?: number[] }
  contributors: { login?: string; total: number }[]
  stargazers_count: number
  forks_count: number
  watchers_count: number
  open_issues_count: number
  default_branch: string
  latest_release: LatestRelease | null
}

export interface PullSummary {
  number: number
  title: string
  user: string | null
  created_at: string
  html_url: string
}

export interface RepoPullsResponse {
  repository: string
  total: number
  pulls: PullSummary[]
}

export interface RepoSecurityResponse {
  repository: string
  branch_protection: "protected" | "unprotected" | "unknown"
  secret_scanning: "enabled" | "disabled"
}

export interface JobOut {
  id: number
  job_type: string
  status: "queued" | "processing" | "done" | "failed"
  result: string | null
  created_at: string
  updated_at: string
}

export interface AuditLogOut {
  id: number
  actor: string
  action: string
  target: string
  payload: string
  created_at: string
}

export interface SavedTokenMeta {
  org: string
  label: string | null
  created_at: string
  updated_at: string
}

export interface InstallationMeta {
  id: number
  account_login: string
  account_type: string
  installation_id: number | null
  created_at: string
}

export interface InstallationLookup {
  account_login: string
  account_type: string
}

export interface SyncInstallationsResponse {
  synced: boolean
  token_ref: string
}

export interface MyOrgMembership {
  org_login: string
  role: "admin" | "member"
}

export interface PendingInvitationSummary {
  // No token here by design — see PendingInvitationSummary in apps/api/src/routers/auth.py.
  org_login: string
  expires_at: string
}

export interface InvitationOut {
  id: number
  org_id: number
  email: string
  status: "pending" | "accepted" | "revoked"
  created_at: string
  accepted_at: string | null
}

export interface InvitationCreateResponse {
  invitation: InvitationOut
  invite_link: string
}

export interface InvitationPreview {
  org_login: string
  status: "pending" | "accepted" | "revoked"
}
