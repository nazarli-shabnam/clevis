export interface MFACheckValue {
  type: "boolean"
  enabled: boolean
}

export interface RatioCheckValue {
  type: "ratio"
  numerator: number
  denominator: number
}

export interface SeverityCountsValue {
  type: "severity_counts"
  critical: number
  high: number
  medium: number
  low: number
}

export type CheckValue = MFACheckValue | RatioCheckValue | SeverityCountsValue | null

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

export interface ScanHistoryEntry {
  id: number
  owner: string
  score: number
  total_checks: number
  failed_checks: number
  created_at: string
}

export type AnalyticsHistoryResponse = ScanHistoryEntry[]

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
  contributors: { author?: { login?: string }; total: number }[]
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
  secret_scanning: "enabled" | "disabled" | "unknown"
}

export interface OrgEvent {
  id: string
  type: string
  actor: string
  actor_avatar: string
  repo: string
  summary: string
  created_at: string
}

export interface OrgEventsResponse {
  org: string
  events: OrgEvent[]
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

export interface GithubOrgMember {
  login: string
  avatar_url: string
  role: "member" | "admin"
  site_admin: boolean
  two_factor_enabled: boolean | null
}

export interface GithubOrgMembersResponse {
  org: string
  members: GithubOrgMember[]
  two_factor_overlay_available: boolean
}

export interface GithubOutsideCollaborator {
  login: string
  avatar_url: string
  repos: string[]
}

export interface GithubOutsideCollaboratorsResponse {
  org: string
  collaborators: GithubOutsideCollaborator[]
  repos_scanned: number
  repos_total: number
}

export interface GithubOrgInvitation {
  login: string | null
  email: string | null
  role: string
  invited_at: string
  inviter: string | null
}

export interface GithubOrgInvitationsResponse {
  org: string
  invitations: GithubOrgInvitation[]
}

export interface GithubMembershipStatus {
  state: "active" | "pending"
  role: "member" | "admin"
}

export interface PrWeekBucket {
  week: string
  opened: number
  merged: number
}

export interface CockpitResponse {
  repo_count: number
  member_count: number
  latest_score: number | null
  score_trend: number[]
  recent_events: OrgEvent[]
  open_pr_count: number
  pr_merge_rate_4w: PrWeekBucket[]
  commit_activity_4w: number[]
  total_cache_size_bytes: number
  cache_job_success_rate: number
}

export interface WorkflowSummary {
  id: number
  name: string
  path: string
  state: string
  last_run_status: string | null
  last_run_conclusion: string | null
  last_run_at: string | null
}

export interface WorkflowsResponse {
  repository: string
  workflows: WorkflowSummary[]
}

export interface RunSummary {
  id: number
  name: string | null
  status: string
  conclusion: string | null
  head_branch: string
  created_at: string
  duration_ms: number | null
}

export interface RunsResponse {
  repository: string
  runs: RunSummary[]
}

export interface DispatchResponse {
  dispatched: boolean
  message: string | null
}
