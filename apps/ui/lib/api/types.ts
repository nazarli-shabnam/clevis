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
  status: "pass" | "fail"
  value: CheckValue
}

export interface AnalyticsOverviewResponse {
  owner: string
  score: number
  total_checks: number
  failed_checks: number
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

export interface MyOrgMembership {
  org_login: string
  role: "admin" | "member"
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
  email: string
  status: string
}
