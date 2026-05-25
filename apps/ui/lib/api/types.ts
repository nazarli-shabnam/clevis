export interface CheckResult {
  id: string
  title: string
  severity: "high" | "medium" | "low"
  remediation: string
  status: "pass" | "fail"
  value: unknown
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
