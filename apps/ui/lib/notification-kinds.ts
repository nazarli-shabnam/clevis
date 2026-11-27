import type { NotificationKind } from "@/lib/api/types"

/** Shared by the bell and the /notifications page so the two cannot drift apart. */
export const KIND_LABEL = {
  critical_alert: "Critical alert",
  score_drop: "Score drop",
  job_failed: "Job failed",
  permission_drift: "Permissions",
  waiver_expired: "Waiver expired",
  waiver_expiring: "Waiver expiring",
  waiver_request: "Waiver request",
  sla_breach: "SLA breach",
  api_token_expiring: "Token expiring",
} as const satisfies Record<NotificationKind, string>

export const KIND_ORDER: NotificationKind[] = [
  "critical_alert",
  "score_drop",
  "job_failed",
  "permission_drift",
  "waiver_expired",
  "waiver_expiring",
  "waiver_request",
  "sla_breach",
  "api_token_expiring",
]

export const KIND_HINT: Record<NotificationKind, string> = {
  critical_alert: "New critical Dependabot and code-scanning alerts",
  score_drop: "Security score drops",
  job_failed: "Failed background jobs (admins)",
  permission_drift: "GitHub App permission changes (admins)",
  waiver_expired: "Check waivers that expired (admins)",
  waiver_expiring: "Check waivers in their last week (admins)",
  waiver_request: "Members asking for a check waiver (admins)",
  sla_breach: "Dependabot alerts past their remediation SLA",
  api_token_expiring: "API tokens close to expiring (admins)",
}
