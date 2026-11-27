/** The instance-configuration form: its fields, how they are grouped, and the ranges the server actually uses.
 *
 * The API accepts any integer of at least 1 (0 for the retention windows) and each background loop then clamps the
 * value into its own range, so a number outside it is silently replaced. The ranges below are those clamps
 * (the `_MIN/_MAX_POLL_SECONDS` of each loop, `services/retention_sweep.py`'s floors, ...): the form refuses a
 * value the server would quietly change, instead of saving it and showing something else later. */

export interface ConfigField {
  key: string
  label: string
  description: string
  type?: string
  options?: { value: string; label: string }[]
  // What the server uses while the key is unset; shown (and saved) for a key that isn't persisted yet.
  defaultValue?: string
}

export const CONFIG_FIELDS: ConfigField[] = [
  { key: "worker_poll_seconds", label: "Worker Poll Interval",    description: "Seconds to wait before polling again when the job queue is empty (queued jobs are picked up back to back).", type: "number" },
  { key: "dependabot_sla_critical_days", label: "Critical Alert SLA", description: "Days a critical Dependabot alert may stay open before it counts as an SLA breach (default 7).", type: "number" },
  { key: "dependabot_sla_high_days", label: "High Alert SLA", description: "Days a high-severity Dependabot alert may stay open before it counts as an SLA breach (default 30).", type: "number" },
  { key: "code_scanning_sla_critical_days", label: "Critical Code Scanning SLA", description: "Days a critical code-scanning alert may stay open before it counts as an SLA breach (default 7).", type: "number" },
  { key: "code_scanning_sla_high_days", label: "High Code Scanning SLA", description: "Days a high-severity code-scanning alert may stay open before it counts as an SLA breach (default 30).", type: "number" },
  { key: "score_hygiene_checks", label: "Score Hygiene Checks (default)", description: "Default for organizations that haven't chosen for themselves: count CODEOWNERS, SECURITY.md, license, stale-branch and unpinned-Action checks toward the security score (off: these checks are not run). Org admins can override it per organization.", type: "boolean", defaultValue: "false" },
  { key: "registration_enabled", label: "Self-Registration",     description: "Allow anyone to create an account via /register.", type: "boolean", defaultValue: "true" },
  {
    key: "digest_cadence",
    label: "Leadership Digest",
    description: "Email org admins a periodic security-score + risk summary. Requires SMTP to be configured.",
    type: "select",
    options: [
      { value: "off", label: "Off" },
      { value: "weekly", label: "Weekly" },
      { value: "monthly", label: "Monthly" },
    ],
  },
  {
    key: "scheduled_scan_cadence",
    label: "Scheduled Security Scans",
    description: "Re-scan every connected organization automatically so scores, alerts and \"changed since last scan\" stay current without anyone clicking Scan. Uses each org's GitHub App installation, never a pasted token. Org admins can switch it on or off per organization. Off by default.",
    type: "select",
    options: [
      { value: "off", label: "Off" },
      { value: "daily", label: "Daily" },
      { value: "weekly", label: "Weekly" },
    ],
  },
  { key: "scheduled_scan_poll_seconds", label: "Scheduled Scan Check Interval", description: "Seconds between checks for an org whose scan is due (default 3600).", type: "number" },
  {
    key: "pr_nudge_cadence",
    label: "Scheduled Stale-PR Nudges",
    description: "Run the stale-PR nudge automatically on every repository an org admin enabled it for, using the org's GitHub App installation (never a pasted token). Uses the nudge mode and stale days below; org admins can switch it per organization. Off by default.",
    type: "select",
    options: [
      { value: "off", label: "Off" },
      { value: "daily", label: "Daily" },
      { value: "weekly", label: "Weekly" },
    ],
  },
  {
    key: "dependabot_triage_cadence",
    label: "Scheduled Dependabot Triage",
    description: "Run Dependabot auto-triage automatically on every repository an org admin enabled it for, using the org's GitHub App installation (never a pasted token). Org admins can switch it per organization. Off by default.",
    type: "select",
    options: [
      { value: "off", label: "Off" },
      { value: "daily", label: "Daily" },
      { value: "weekly", label: "Weekly" },
    ],
  },
  {
    key: "stale_cache_clear_cadence",
    label: "Scheduled Stale-Cache Clear",
    description: "Delete GitHub Actions caches that nobody has used for the number of days an org admin set, on every repository they enabled it for, using the org's GitHub App installation (never a pasted token). Org admins can switch it per organization. Off by default.",
    type: "select",
    options: [
      { value: "off", label: "Off" },
      { value: "daily", label: "Daily" },
      { value: "weekly", label: "Weekly" },
    ],
  },
  { key: "automation_poll_seconds", label: "Scheduled Automation Check Interval", description: "Seconds between checks for a due scheduled nudge or triage run (default 3600).", type: "number" },
  { key: "digest_poll_seconds", label: "Digest Check Interval", description: "Seconds between checks for a due leadership digest.", type: "number" },
  {
    key: "pr_nudge_mode",
    label: "Stale-PR Nudge Mode",
    description: "What \"Nudge stale PRs\" does on the Pull Requests page.",
    type: "select",
    options: [
      { value: "comment", label: "Comment" },
      { value: "label", label: "Add needs-review label" },
      { value: "off", label: "Off" },
    ],
  },
  { key: "pr_nudge_stale_days", label: "Stale-PR Threshold", description: "Days without review activity before a PR counts as stale.", type: "number" },
  { key: "gap_heal_poll_seconds", label: "Activity Gap-Heal Interval", description: "Seconds between sweeps that re-backfill stale activity (default 900).", type: "number" },
  { key: "gap_heal_stale_hours", label: "Activity Stale After", description: "Hours since the last activity sync before a backfill is re-queued (default 6).", type: "number" },
  { key: "membership_reconcile_poll_seconds", label: "Membership Reconcile Interval", description: "Seconds between org-membership reconciliation sweeps.", type: "number" },
  { key: "membership_reconcile_stale_hours", label: "Membership Stale After", description: "Hours since the last membership sync before it's reconciled again.", type: "number" },
  { key: "webhook_requeue_poll_seconds", label: "Webhook Re-queue Interval", description: "Seconds between retries of webhook deliveries that failed to queue.", type: "number" },
  { key: "retention_webhook_deliveries_days", label: "Keep Webhook Deliveries (days)", description: "Days to keep webhook payloads once they are processed or given up on (default 30). Deliveries still waiting to be queued are never deleted.", type: "number", defaultValue: "30" },
  { key: "retention_jobs_days", label: "Keep Finished Jobs (days)", description: "Days to keep finished (done or failed) background jobs (default 30).", type: "number", defaultValue: "30" },
  { key: "retention_repo_events_days", label: "Keep Activity Events (days)", description: "Days to keep stored GitHub activity events (default 180). Daily activity counts, security alerts, the audit log and scan history are always kept.", type: "number", defaultValue: "180" },
]

export interface ConfigLimits {
  min: number
  max: number
  /** 0 is allowed and switches the purge off (retention windows only); anything else must still be min..max. */
  zeroOff?: boolean
}

const SECONDS_FAST = { min: 60, max: 3600 }
const SECONDS_SLOW = { min: 300, max: 86_400 }
const HOURS = { min: 1, max: 168 }
const SLA_DAYS = { min: 1, max: 3650 }

// Each range mirrors a constant on the server; if one of those moves, move it here too:
//   *_poll_seconds  -> the `_MIN_POLL_SECONDS` / `_MAX_POLL_SECONDS` of apps/api/src/services/<name>_loop.py
//   *_stale_hours   -> `max(1, min(168, ...))` in gap_heal_sweep.py and membership_reconcile_sweep.py
//   pr_nudge_stale_days -> `configured_stale_days` in pr_nudge.py
//   *_sla_*_days    -> `sla_days` in dependabot_burndown.py (and the 3650 cap in routers/config.py)
//   retention_*     -> the `min_days` of each policy in retention_sweep.py
export const CONFIG_LIMITS: Record<string, ConfigLimits> = {
  worker_poll_seconds: { min: 1, max: 30 },
  gap_heal_poll_seconds: SECONDS_FAST,
  gap_heal_stale_hours: HOURS,
  membership_reconcile_poll_seconds: SECONDS_FAST,
  membership_reconcile_stale_hours: HOURS,
  webhook_requeue_poll_seconds: SECONDS_FAST,
  scheduled_scan_poll_seconds: SECONDS_SLOW,
  automation_poll_seconds: SECONDS_SLOW,
  digest_poll_seconds: SECONDS_SLOW,
  pr_nudge_stale_days: { min: 1, max: 365 },
  dependabot_sla_critical_days: SLA_DAYS,
  dependabot_sla_high_days: SLA_DAYS,
  code_scanning_sla_critical_days: SLA_DAYS,
  code_scanning_sla_high_days: SLA_DAYS,
  // Floors are what the retention sweep raises a smaller value to (WEBHOOK_DELIVERIES, JOBS, REPO_EVENTS).
  retention_webhook_deliveries_days: { min: 30, max: 3650, zeroOff: true },
  retention_jobs_days: { min: 8, max: 3650, zeroOff: true },
  retention_repo_events_days: { min: 100, max: 3650, zeroOff: true },
}

export interface ConfigGroup {
  id: string
  label: string
  keys: string[]
}

export const CONFIG_GROUPS: ConfigGroup[] = [
  { id: "access", label: "Access and scoring", keys: ["registration_enabled", "score_hygiene_checks"] },
  {
    id: "sla",
    label: "Alert SLAs",
    keys: ["dependabot_sla_critical_days", "dependabot_sla_high_days", "code_scanning_sla_critical_days", "code_scanning_sla_high_days"],
  },
  {
    id: "schedules",
    label: "Schedules",
    keys: [
      "scheduled_scan_cadence",
      "scheduled_scan_poll_seconds",
      "digest_cadence",
      "digest_poll_seconds",
      "pr_nudge_cadence",
      "dependabot_triage_cadence",
      "stale_cache_clear_cadence",
      "automation_poll_seconds",
    ],
  },
  { id: "automation", label: "Automation", keys: ["pr_nudge_mode", "pr_nudge_stale_days"] },
  {
    id: "sync",
    label: "Background sync",
    keys: [
      "worker_poll_seconds",
      "gap_heal_poll_seconds",
      "gap_heal_stale_hours",
      "membership_reconcile_poll_seconds",
      "membership_reconcile_stale_hours",
      "webhook_requeue_poll_seconds",
    ],
  },
  {
    id: "retention",
    label: "Retention",
    keys: ["retention_webhook_deliveries_days", "retention_jobs_days", "retention_repo_events_days"],
  },
]

/** The allowed range of a number field, as text under the input ("60 to 3600", "0 (keep forever) or 30 to 3650"). */
export function limitsText(key: string): string | null {
  const l = CONFIG_LIMITS[key]
  if (!l) return null
  return l.zeroOff ? `0 (keep forever) or ${l.min} to ${l.max}` : `${l.min} to ${l.max}`
}

/** Why a value cannot be saved for this field, or null when it can. Only number fields have a rule. */
export function validateConfigValue(field: ConfigField, value: string): string | null {
  if (field.type !== "number") return null
  const text = value.trim()
  if (!/^-?\d+$/.test(text)) return "Enter a whole number."
  const n = Number(text)
  const l = CONFIG_LIMITS[field.key]
  if (!l) return null
  if (l.zeroOff && n === 0) return null
  if (n < l.min || n > l.max) return `Use ${limitsText(field.key)}; the server would otherwise change it.`
  return null
}
