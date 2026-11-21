import { safeStorage } from "@/lib/safe-storage"

// Long operations (a scan, bulk remediation, bulk branch protection, Dependabot triage) run as server-side jobs
// that `postJob` polls. The poller only lives as long as the page, so the job's id is kept here: after a reload,
// tab close or navigation the app can find the job again and re-attach instead of losing it (and the user starting
// a second write job). Only the starter can read a job (`GET /jobs/{id}` is scoped to its actor), so ids belonging
// to another account simply 404 on re-attach and are dropped.

const KEY = "clevis:running-jobs"
// postJob gives up after 30 minutes; anything older than that is not worth re-attaching to.
const MAX_AGE_MS = 35 * 60_000

export interface TrackedJob {
  jobId: number
  label: string
  org: string | null
  startedAt: number
}

function isTrackedJob(j: unknown): j is TrackedJob {
  const t = j as Partial<TrackedJob> | null
  return (
    typeof t?.jobId === "number" &&
    typeof t.label === "string" &&
    (t.org === null || typeof t.org === "string") &&
    typeof t.startedAt === "number"
  )
}

function read(): TrackedJob[] {
  try {
    const parsed = JSON.parse(safeStorage.getItem(KEY) ?? "[]")
    return Array.isArray(parsed) ? parsed.filter(isTrackedJob) : []
  } catch {
    return []
  }
}

function write(jobs: TrackedJob[]): void {
  if (jobs.length === 0) safeStorage.removeItem(KEY)
  else safeStorage.setItem(KEY, JSON.stringify(jobs))
}

export function trackJob(job: TrackedJob): void {
  write([...read().filter((j) => j.jobId !== job.jobId), job])
}

export function forgetJob(jobId: number): void {
  write(read().filter((j) => j.jobId !== jobId))
}

// Bumped whenever the stored jobs are cleared (sign-out), so a start that was in flight across the clear can tell
// it belongs to the previous identity and must not be written back.
let generation = 0
export const jobGeneration = () => generation

export function clearTrackedJobs(): void {
  generation++
  safeStorage.removeItem(KEY)
}

/** Jobs started from this browser that may still be running (stale entries are dropped as they are read). */
export function listTrackedJobs(now = Date.now()): TrackedJob[] {
  const all = read()
  const fresh = all.filter((j) => now - j.startedAt < MAX_AGE_MS)
  if (fresh.length !== all.length) write(fresh)
  return fresh
}

// Jobs a `postJob` in this page lifetime is already waiting on: re-attaching to those would double the polling.
const live = new Set<number>()
export const markLive = (jobId: number) => void live.add(jobId)
export const unmarkLive = (jobId: number) => void live.delete(jobId)
export const isLive = (jobId: number) => live.has(jobId)

const LABELS: [RegExp, string][] = [
  [/\/analytics\/overview$/, "security scan"],
  [/\/security\/remediate\/bulk$/, "bulk fix"],
  [/\/branch-protection\/bulk$/, "bulk branch-protection change"],
  [/\/dependabot-triage$/, "Dependabot triage"],
]

/** A short human label and the org (or account) an operation targets, from the endpoint and its body. */
export function describeJob(path: string, body: unknown): { label: string; org: string | null } {
  const label = LABELS.find(([re]) => re.test(path))?.[1] ?? "background operation"
  const fromPath = /^\/orgs\/([^/]+)\//.exec(path)?.[1]
  const owner = (body as { owner?: unknown } | null)?.owner
  return { label, org: fromPath ? decodeURIComponent(fromPath) : typeof owner === "string" ? owner : null }
}
