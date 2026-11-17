import { EMPTY_FORM, type FilterForm } from "@/lib/audit-filters"
import { PULL_SORT_LABELS, type PullSort } from "@/lib/pull-triage"

/** Filters and sort of a list page, as the query string keys it owns. Like `repoViewToParams`, defaults are
 * left out so an unfiltered page keeps a clean URL, and a malformed value falls back to its default. */
export interface UrlViewCodec<T> {
  /** Every query key this view writes; the hook clears them before writing, and leaves all others alone. */
  keys: readonly string[]
  toParams: (view: T) => URLSearchParams
  fromParams: (params: URLSearchParams) => T
}

function pick<T extends string>(params: URLSearchParams, key: string, allowed: readonly T[], fallback: T): T {
  const value = params.get(key)
  return allowed.includes(value as T) ? (value as T) : fallback
}

// ── Pull requests ──────────────────────────────────────────────────────────────────────────────────────
export type PullStatusFilter = "all" | "stale" | "draft" | "open"
export type PullGroupBy = "repo" | "author"

export interface PullView {
  status: PullStatusFilter
  /** "" = every author. */
  author: string
  sort: PullSort
  group: PullGroupBy
}

export const DEFAULT_PULL_VIEW: PullView = { status: "all", author: "", sort: "newest", group: "repo" }

export const pullViewCodec: UrlViewCodec<PullView> = {
  keys: ["status", "author", "sort", "group"],
  toParams: (v) => {
    const p = new URLSearchParams()
    if (v.status !== DEFAULT_PULL_VIEW.status) p.set("status", v.status)
    if (v.author) p.set("author", v.author)
    if (v.sort !== DEFAULT_PULL_VIEW.sort) p.set("sort", v.sort)
    if (v.group !== DEFAULT_PULL_VIEW.group) p.set("group", v.group)
    return p
  },
  fromParams: (p) => ({
    status: pick<PullStatusFilter>(p, "status", ["stale", "draft", "open"], "all"),
    author: p.get("author") ?? "",
    sort: pick<PullSort>(p, "sort", Object.keys(PULL_SORT_LABELS) as PullSort[], "newest"),
    group: pick<PullGroupBy>(p, "group", ["author"], "repo"),
  }),
}

// ── Audit log ──────────────────────────────────────────────────────────────────────────────────────────
/** The applied filters (not the form being typed in) plus the exact-action dropdown. */
export interface AuditView {
  applied: FilterForm
  action: string
}

export const auditViewCodec: UrlViewCodec<AuditView> = {
  keys: ["actor", "prefix", "target", "from", "to", "action"],
  toParams: ({ applied: f, action }) => {
    const p = new URLSearchParams()
    if (f.actor) p.set("actor", f.actor)
    if (f.actionPrefix) p.set("prefix", f.actionPrefix)
    if (f.target) p.set("target", f.target)
    if (f.from) p.set("from", f.from)
    if (f.to) p.set("to", f.to)
    if (action) p.set("action", action)
    return p
  },
  fromParams: (p) => ({
    applied: {
      ...EMPTY_FORM,
      actor: p.get("actor") ?? "",
      actionPrefix: p.get("prefix") ?? "",
      target: p.get("target") ?? "",
      from: p.get("from") ?? "",
      to: p.get("to") ?? "",
    },
    action: p.get("action") ?? "",
  }),
}

// ── Org members roster ─────────────────────────────────────────────────────────────────────────────────
export type MemberRoleFilter = "all" | "member" | "admin"

export interface MembersView {
  search: string
  role: MemberRoleFilter
  /** Only members whose 2FA is known to be off. */
  twoFaOff: boolean
}

export const DEFAULT_MEMBERS_VIEW: MembersView = { search: "", role: "all", twoFaOff: false }

export const membersViewCodec: UrlViewCodec<MembersView> = {
  keys: ["q", "role", "no2fa"],
  toParams: (v) => {
    const p = new URLSearchParams()
    if (v.search) p.set("q", v.search)
    if (v.role !== "all") p.set("role", v.role)
    if (v.twoFaOff) p.set("no2fa", "1")
    return p
  },
  fromParams: (p) => ({
    search: p.get("q") ?? "",
    role: pick<MemberRoleFilter>(p, "role", ["member", "admin"], "all"),
    twoFaOff: p.get("no2fa") === "1",
  }),
}
