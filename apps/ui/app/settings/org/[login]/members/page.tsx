"use client"

import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useEffect, useMemo, useState } from "react"
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { DataTable, type DataTableColumn } from "@/components/ui/data-table"
import { ArrowsClockwise, CircleNotch, EnvelopeSimple, PaperPlaneTilt, Warning, X } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { addRevokingId, isRevoking, removeRevokingId } from "@/lib/revoke-pending"
import { exactTime, relativeTime } from "@/lib/format"
import { effectiveInvitationStatus, invitationExpiry, type ExpiryTone } from "@/lib/invitation-expiry"
import { CopyButton } from "@/components/copy-button"
import { orgRoleFor } from "@/lib/members-href"
import { SectionError } from "@/components/section-error"
import { HygieneScoringCard } from "@/components/hygiene-scoring-card"
import { ScheduledScansCard } from "@/components/scheduled-scans-card"
import { WaiversCard } from "@/components/waivers-card"
import { EvidenceExportCard } from "@/components/evidence-export-card"
import { MemberAccessSheet } from "@/components/member-access-sheet"
import { BadgeSettingsCard } from "@/components/badge-settings-card"
import type { GithubOrgMember, InvitationBulkItem, InvitationOut, MyOrgMembership } from "@/lib/api/types"
import { githubWebUrl } from "@/lib/github-web"

function memberColumns(onReview: ((login: string) => void) | null): DataTableColumn<GithubOrgMember>[] {
  return [
  {
    key: "login",
    header: "Member",
    sortValue: (m) => m.login.toLowerCase(),
    csvValue: (m) => m.login,
    render: (m) => (
      <div className="flex items-center gap-2">
        {/* Decorative: the login is the adjacent link text, so empty alt avoids a duplicate announcement. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={m.avatar_url} alt="" className="size-5 rounded-full" />
        <a
          href={githubWebUrl(m.login)}
          target="_blank"
          rel="noreferrer"
          className="text-foreground/80 hover:text-foreground"
        >
          {m.login}
        </a>
      </div>
    ),
  },
  {
    key: "role",
    header: "Role",
    sortValue: (m) => m.role,
    csvValue: (m) => m.role,
    cellClassName: "text-muted-foreground capitalize",
    render: (m) => m.role,
  },
  {
    key: "two_factor_enabled",
    header: "2FA",
    // Groups unknown (null) between the two known states -- there's no natural order for "we don't know".
    sortValue: (m) => (m.two_factor_enabled === true ? 1 : m.two_factor_enabled === false ? -1 : 0),
    // Unknown stays distinguishable from "no": an empty cell would read as a failed check.
    csvValue: (m) => (m.two_factor_enabled === true ? "yes" : m.two_factor_enabled === false ? "no" : "unknown"),
    render: (m) => (
      <>
        {m.two_factor_enabled === true && <span className="stat-chip">✓ 2FA</span>}
        {m.two_factor_enabled === false && (
          <span className="stat-chip text-red-400 border-red-500/30">No 2FA</span>
        )}
      </>
    ),
  },
  ...(onReview
    ? [
        {
          key: "review",
          header: "",
          align: "right" as const,
          render: (m: GithubOrgMember) => (
            <Button size="sm" variant="outline" onClick={() => onReview(m.login)} aria-label={`Review access for ${m.login}`}>
              Review access
            </Button>
          ),
        },
      ]
    : []),
  ]
}

const ROSTER_TABS = [
  { id: "members", label: "Members" },
  { id: "outside", label: "Outside" },
  { id: "pending", label: "Pending GitHub invitations" },
  { id: "audit", label: "Audit" },
] as const

type RosterTabId = (typeof ROSTER_TABS)[number]["id"]

function GithubRoster({ orgLogin, canReview }: { orgLogin: string; canReview: boolean }) {
  const [reviewLogin, setReviewLogin] = useState<string | null>(null)
  const columns = useMemo(() => memberColumns(canReview ? setReviewLogin : null), [canReview])
  const router = useRouter()
  const searchParams = useSearchParams()
  const rawTab = searchParams.get("roster") ?? "members"
  const tab = (ROSTER_TABS.some((t) => t.id === rawTab) ? rawTab : "members") as RosterTabId
  const [search, setSearch] = useState("")
  const [roleFilter, setRoleFilter] = useState<"all" | "member" | "admin">("all")

  function setTab(id: RosterTabId) {
    const params = new URLSearchParams(searchParams.toString())
    if (id === "members") params.delete("roster")
    else params.set("roster", id)
    router.replace(`?${params.toString()}`, { scroll: false })
  }

  // Falls back to a PAT saved for this org when no App installation covers it; a failed
  // resolution just means the App installation (if any) is used.
  const tokenQuery = useQuery({
    queryKey: ["tokens.resolve", orgLogin],
    queryFn: () => api.tokens.resolve(orgLogin),
    retry: false,
  })
  const token = tokenQuery.data?.token

  // Wait for token resolution to settle so a saved PAT isn't missed on the first request
  // (queryKey excludes token, so a late token wouldn't trigger a refetch).
  const tokenSettled = !tokenQuery.isLoading

  const membersQuery = useQuery({
    queryKey: ["collab", "members", orgLogin, roleFilter],
    queryFn: () => api.collab.members(orgLogin, roleFilter, token),
    enabled: tab === "members" && tokenSettled,
    // Keep the previous role filter's rows on screen while the new one loads instead of
    // flashing an empty table.
    placeholderData: keepPreviousData,
  })
  const outsideQuery = useQuery({
    queryKey: ["collab", "outside", orgLogin],
    queryFn: () => api.collab.outsideCollaborators(orgLogin, token),
    enabled: tab === "outside" && tokenSettled,
  })
  const pendingQuery = useQuery({
    queryKey: ["collab", "pending", orgLogin],
    queryFn: () => api.collab.invitations(orgLogin, token),
    enabled: tab === "pending" && tokenSettled,
  })
  const permissionAuditQuery = useQuery({
    queryKey: ["collab", "permission-audit", orgLogin],
    queryFn: () => api.collab.permissionAudit(orgLogin, token),
    enabled: tab === "audit" && tokenSettled,
  })
  const inactiveMembersQuery = useQuery({
    queryKey: ["collab", "inactive-members", orgLogin],
    queryFn: () => api.collab.inactiveMembers(orgLogin, 30, token),
    enabled: tab === "audit" && tokenSettled,
  })

  const activeQuery =
    tab === "members" ? membersQuery
    : tab === "outside" ? outsideQuery
    : tab === "pending" ? pendingQuery
    : permissionAuditQuery

  const outsideWithElevatedRepos = new Map<string, string[]>()
  for (const repo of permissionAuditQuery.data?.repos ?? []) {
    for (const c of repo.collaborators) {
      if (c.is_outside_collaborator && (c.permission === "write" || c.permission === "maintain" || c.permission === "admin")) {
        const existing = outsideWithElevatedRepos.get(c.login)
        if (existing) existing.push(repo.repo)
        else outsideWithElevatedRepos.set(c.login, [repo.repo])
      }
    }
  }

  const filteredMembers = (membersQuery.data?.members ?? []).filter((m) =>
    m.login.toLowerCase().includes(search.toLowerCase()),
  )
  // Derived from filteredMembers so the count matches the visible table -- a stale org-wide
  // total next to a filtered table reads as a data-integrity bug for a 2FA-compliance number.
  const membersWithout2fa = filteredMembers.filter((m) => m.two_factor_enabled === false).length
  // roleFilter is a server-side refetch (in the queryKey); `search` is client-side.
  const isRefetchingByRole = membersQuery.isFetching && !membersQuery.isLoading

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">GitHub organization roster</span>
      </div>

      <div className="px-4 py-2.5 border-b border-border flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5">
          {ROSTER_TABS.map((t) => {
            const active = tab === t.id
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                aria-pressed={active}
                className={`text-xs font-medium px-2.5 py-1 rounded-md border transition-colors ${
                  active
                    ? "border-border bg-elevated text-foreground"
                    : "border-transparent text-muted-foreground hover:bg-elevated"
                }`}
              >
                {t.label}
              </button>
            )
          })}
        </div>
        {tab === "members" && (
          <>
            <Input
              aria-label="Search members by login"
              placeholder="Search by login…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="max-w-48 h-7 text-xs"
            />
            <select
              aria-label="Filter by role"
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value as "all" | "member" | "admin")}
              className="text-xs card text-muted-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="all">All roles</option>
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            {isRefetchingByRole && (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <CircleNotch className="size-3 animate-spin" /> Updating…
              </span>
            )}
          </>
        )}
      </div>

      {/* Each audit section has its own loading/error state, so a failed permission audit
          doesn't hide inactive-members data that loaded fine. */}
      {tab !== "audit" && activeQuery.isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : tab !== "audit" && activeQuery.isError ? (
        <div className="px-4 py-6">
          <p className="text-xs text-destructive">{activeQuery.error.message}</p>
        </div>
      ) : tab === "members" ? (
        filteredMembers.length === 0 ? (
          <div className="px-4 py-8">
            <p className="text-sm text-muted-foreground">No members found</p>
          </div>
        ) : (
          <>
            <DataTable
              columns={columns}
              data={filteredMembers}
              getRowKey={(m) => m.login}
              exportCsv={{ name: `members-${orgLogin}` }}
            />
            {membersQuery.data?.two_factor_overlay_available && (
              <div className="px-4 py-2.5 border-t border-border">
                <span className="text-xs text-muted-foreground">Members without 2FA: {membersWithout2fa}</span>
              </div>
            )}
          </>
        )
      ) : tab === "outside" ? (
        (outsideQuery.data?.collaborators.length ?? 0) === 0 ? (
          <div className="px-4 py-8">
            <p className="text-sm text-muted-foreground">No outside collaborators</p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Collaborator</th>
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Repos</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {outsideQuery.data!.collaborators.map((c) => (
                    <tr key={c.login}>
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          {/* Decorative: the login is the adjacent text, so empty alt avoids a duplicate announcement. */}
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={c.avatar_url} alt="" className="size-5 rounded-full" />
                          <span className="text-foreground/80">{c.login}</span>
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground">
                        <div className="flex flex-wrap gap-1">
                          {c.repos.map((r) => (
                            <span key={r} className="stat-chip">{r}</span>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {outsideQuery.data && outsideQuery.data.repos_scanned < outsideQuery.data.repos_total && (
              <div className="px-4 py-2.5 border-t border-border">
                <span className="text-xs text-muted-foreground">
                  Scanned {outsideQuery.data.repos_scanned} of {outsideQuery.data.repos_total} repos
                </span>
              </div>
            )}
          </>
        )
      ) : tab === "pending" ? ((pendingQuery.data?.invitations.length ?? 0) === 0 ? (
          <div className="px-4 py-8">
            <p className="text-sm text-muted-foreground">No pending GitHub invitations</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Invitee</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Role</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Invited</th>
                  <th className="text-left text-muted-foreground font-medium px-4 py-2">Inviter</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {pendingQuery.data!.invitations.map((inv, i) => (
                  <tr key={i}><td className="px-4 py-2.5 text-foreground/80">{inv.login ?? inv.email}</td><td className="px-4 py-2.5 text-muted-foreground capitalize">{inv.role}</td><td className="px-4 py-2.5 text-muted-foreground">{relativeTime(inv.invited_at)}</td><td className="px-4 py-2.5 text-muted-foreground">{inv.inviter ?? "—"}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : (
        <>
          {outsideWithElevatedRepos.size > 0 && (
            <div className="px-4 py-3 border-b border-border bg-yellow-500/5">
              <p className="text-xs font-medium text-yellow-400 flex items-center gap-1.5 mb-1.5"><Warning className="size-3.5" /> Access Risk: {outsideWithElevatedRepos.size} outside collaborator{outsideWithElevatedRepos.size === 1 ? "" : "s"} with write/admin access</p>
              <ul className="text-xs text-muted-foreground flex flex-col gap-0.5">
                {[...outsideWithElevatedRepos.entries()].map(([login, repos]) => (
                  <li key={login}>{login} · {repos.join(", ")}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="px-4 py-2.5 border-b border-border">
            <span className="text-xs font-medium text-foreground">Permission audit</span>
            <span className="text-xs text-muted-foreground ml-2">
              generated {permissionAuditQuery.data ? relativeTime(permissionAuditQuery.data.generated_at) : "—"}
              {permissionAuditQuery.data && permissionAuditQuery.data.repos_scanned < permissionAuditQuery.data.repos_total && (
                <> · scanned {permissionAuditQuery.data.repos_scanned} of {permissionAuditQuery.data.repos_total} repos</>
              )}
            </span>
          </div>
          {permissionAuditQuery.isLoading ? (
            <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Loading…
            </div>
          ) : permissionAuditQuery.isError ? (
            <div className="px-4 py-6">
              <p className="text-xs text-destructive">{permissionAuditQuery.error.message}</p>
            </div>
          ) : (
            <div className="overflow-x-auto max-h-96 overflow-y-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Repo</th>
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Collaborator</th>
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Affiliation</th>
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Permission</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {(permissionAuditQuery.data?.repos ?? []).flatMap((repo) =>
                    repo.collaborators.map((c) => (
                      <tr key={`${repo.repo}-${c.login}`} className={c.is_outside_collaborator && (c.permission === "write" || c.permission === "maintain" || c.permission === "admin") ? "bg-yellow-500/5" : ""}>
                        <td className="px-4 py-2 font-mono text-muted-foreground truncate max-w-[10rem]">{repo.repo}</td>
                        <td className="px-4 py-2 text-foreground/80">{c.login}</td>
                        <td className="px-4 py-2 text-muted-foreground">{c.affiliation}</td>
                        <td className="px-4 py-2 text-muted-foreground capitalize">{c.permission}</td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          )}

          <div className="px-4 py-2.5 border-b border-t border-border">
            <span className="text-xs font-medium text-foreground">Inactive members (30d+)</span>
            {inactiveMembersQuery.data && inactiveMembersQuery.data.sampled_repos.length > 0 && (
              <span className="text-xs text-muted-foreground ml-2">
                sampled from {inactiveMembersQuery.data.sampled_repos.join(", ")} — approximation, not exact
              </span>
            )}
            {inactiveMembersQuery.data?.members_total != null &&
              inactiveMembersQuery.data.members_checked != null &&
              inactiveMembersQuery.data.members_checked < inactiveMembersQuery.data.members_total && (
                <p className="text-xs text-muted-foreground mt-1">
                  Checked {inactiveMembersQuery.data.members_checked} of {inactiveMembersQuery.data.members_total} members
                  (live lookups are limited until the GitHub App&rsquo;s activity data has synced), so this list is incomplete.
                </p>
              )}
          </div>
          {inactiveMembersQuery.isLoading ? (
            <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Loading…
            </div>
          ) : inactiveMembersQuery.error ? (
            // Distinct from the "no inactive members" empty state -- an error must not read as
            // "org is clean" (a false-negative access-risk signal).
            <div className="px-4 py-6">
              <p className="text-xs text-destructive">{inactiveMembersQuery.error.message}</p>
            </div>
          ) : (inactiveMembersQuery.data?.members.length ?? 0) === 0 ? (
            <div className="px-4 py-6">
              <p className="text-sm text-muted-foreground">No inactive members found (within the sampled repos)</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <tbody className="divide-y divide-border">
                  {inactiveMembersQuery.data!.members.map((m) => (
                    <tr key={m.login}>
                      <td className="px-4 py-2 text-foreground/80">{m.login}</td>
                      <td className="px-4 py-2 text-muted-foreground capitalize">{m.role}</td>
                      <td className="px-4 py-2 text-muted-foreground">
                        {m.last_commit_days_ago != null
                          ? `last commit ${m.last_commit_days_ago}d ago in ${m.last_commit_repo}`
                          : "no commits found in sampled repos"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {canReview && <MemberAccessSheet orgLogin={orgLogin} login={reviewLogin} onClose={() => setReviewLogin(null)} />}
    </div>
  )
}

const EXPIRY_TONE_CLASS: Record<ExpiryTone, string> = {
  none: "text-muted-foreground",
  normal: "text-muted-foreground",
  soon: "text-yellow-400",
  expired: "text-destructive",
}

function ExpiryCell({ inv, now }: { inv: InvitationOut; now: number }) {
  const { label, tone } = invitationExpiry(inv, now)
  return (
    <td className={`px-4 py-2.5 whitespace-nowrap ${EXPIRY_TONE_CLASS[tone]}`} title={tone === "none" ? undefined : exactTime(inv.expires_at)}>
      {/* Colour alone isn't a signal: add an icon, and say it in text for screen readers. */}
      {tone === "soon" && <Warning className="inline size-3 mr-1 align-[-1px]" aria-hidden="true" />}
      {label}
      {tone === "soon" && <span className="sr-only"> (expiring soon)</span>}
    </td>
  )
}

// Mirrors MAX_BULK_INVITES in apps/api/src/schemas/invitation.py; the API 422s a longer list.
const MAX_BULK_INVITES = 50

// Addresses may be pasted separated by commas, semicolons, spaces or newlines. A repeat (compared
// case-insensitively, as the API does) is dropped so the count shown matches the rows reported.
const splitEmails = (text: string) => {
  const seen = new Set<string>()
  return text.split(/[\s,;]+/).filter((address) => {
    const key = address.toLowerCase()
    if (!address || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const BULK_STATUS_LABEL: Record<InvitationBulkItem["status"], string> = {
  created: "invited",
  already_pending: "already has a pending invitation",
  invalid: "not a valid email address",
  failed: "couldn't be invited, try again",
}

export default function OrgMembersPage() {
  const params = useParams<{ login: string }>()
  const orgLogin = params.login
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [lastLink, setLastLink] = useState<{ id: number; link: string } | null>(null)
  const [bulkResults, setBulkResults] = useState<InvitationBulkItem[] | null>(null)
  // Regenerating, resending and revoking all kill an invitation's current link, so drop it from
  // the page. Only that invitation's: the other links of a bulk result may not be copied yet.
  const clearDisplayedLink = (id: number) => {
    setLastLink((prev) => (prev?.id === id ? null : prev))
    setBulkResults((prev) => prev?.map((r) => (r.invitation?.id === id ? { ...r, invite_link: null } : r)) ?? null)
  }
  // Re-evaluate expiry labels as time passes, so a page left open doesn't keep showing "in 5 minutes".
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  // Inviting and revoking are admin-only on the API. Look up the caller's role so a plain member
  // gets an explanation instead of a form that can only 403. If the lookup itself fails, don't
  // guess: show the admin UI and let the API's verdict surface through its own error states.
  const membershipsQuery = useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })
  const rolePending = membershipsQuery.isLoading
  const role = membershipsQuery.data ? orgRoleFor(membershipsQuery.data, orgLogin) : undefined
  const notAdmin = role !== undefined && role !== "admin"

  const invitationsQuery = useQuery<InvitationOut[]>({
    queryKey: ["invitations", orgLogin],
    queryFn: () => api.invitations.list(orgLogin),
    enabled: !rolePending && !notAdmin,
  })
  const invitations = invitationsQuery.data ?? []
  const isLoading = invitationsQuery.isLoading

  const [revokingIds, setRevokingIds] = useState<Set<number>>(() => new Set())

  const addresses = splitEmails(email)
  const invite = useMutation({
    // One address goes through the single endpoint (so a duplicate is an error); several go through
    // the bulk one, which reports each address on its own.
    mutationFn: async () => {
      if (addresses.length > 1) return { many: await api.invitations.createMany(orgLogin, addresses) }
      return { one: await api.invitations.create(orgLogin, addresses[0]) }
    },
    onSuccess: (data) => {
      setLastLink(data.one ? { id: data.one.invitation.id, link: data.one.invite_link } : null)
      setBulkResults(data.many?.results ?? null)
      // Keep any address that failed in the box so it can be resubmitted as is.
      setEmail((data.many?.results ?? []).filter((r) => r.status === "failed").map((r) => r.email).join("\n"))
      queryClient.invalidateQueries({ queryKey: ["invitations", orgLogin] })
    },
  })

  // Regenerate / resend, tracked per invitation like revoke so concurrent rows don't clobber each other.
  const [rowNotes, setRowNotes] = useState<Record<number, { ok: boolean; text: string }>>({})
  const [reissuingIds, setReissuingIds] = useState<Set<number>>(() => new Set())
  const reissue = useMutation({
    mutationFn: async ({ id, kind }: { id: number; kind: "regenerate" | "resend" }) => {
      if (kind === "regenerate") return { link: (await api.invitations.regenerate(orgLogin, id)).invite_link }
      await api.invitations.resend(orgLogin, id)
      return { link: null }
    },
    onMutate: ({ id }) => {
      setRowNotes(({ [id]: _cleared, ...rest }) => rest)
      setReissuingIds((prev) => addRevokingId(prev, id))
    },
    onError: (error, { id, kind }) => {
      // A failed send (502) comes after the token was rotated (and committed), so the link on screen
      // is dead too and the cached row is stale. No status at all (timeout, lost response) means we
      // can't tell whether it rotated. A 404/409 is refused before any rotation: that link still works.
      const status = (error as { status?: number }).status
      if (status === undefined || (kind === "resend" && status === 502)) clearDisplayedLink(id)
      if (status === undefined || status === 502) queryClient.invalidateQueries({ queryKey: ["invitations", orgLogin] })
      setRowNotes((prev) => ({ ...prev, [id]: { ok: false, text: error.message } }))
    },
    onSettled: (_data, _error, { id }) => setReissuingIds((prev) => removeRevokingId(prev, id)),
    onSuccess: (data, { id, kind }) => {
      clearDisplayedLink(id)
      if (kind === "regenerate" && data.link) {
        setLastLink({ id, link: data.link })
        setRowNotes((prev) => ({ ...prev, [id]: { ok: true, text: "New link ready above. The old one no longer works." } }))
      } else {
        setRowNotes((prev) => ({ ...prev, [id]: { ok: true, text: "Email sent with a new link. The old one no longer works." } }))
      }
      queryClient.invalidateQueries({ queryKey: ["invitations", orgLogin] })
    },
  })

  // useMutation only retains the latest call's error, so concurrent revokes would overwrite
  // each other's failures; track them per invitation instead.
  const [revokeErrors, setRevokeErrors] = useState<Record<number, string>>({})

  const revoke = useMutation({
    mutationFn: (id: number) => api.invitations.revoke(orgLogin, id),
    onMutate: (id) => {
      setRevokeErrors(({ [id]: _cleared, ...rest }) => rest)
      setRevokingIds((prev) => addRevokingId(prev, id))
    },
    onError: (error, id) => setRevokeErrors((prev) => ({ ...prev, [id]: error.message })),
    onSettled: (_data, _error, id) => setRevokingIds((prev) => removeRevokingId(prev, id)),
    onSuccess: (_data, id) => {
      clearDisplayedLink(id)
      setRowNotes(({ [id]: _cleared, ...rest }) => rest)
      queryClient.invalidateQueries({ queryKey: ["invitations", orgLogin] })
    },
  })

  // Revoke and reissue race on the same row (a late regenerate would restore a revoked link), so any
  // pending action on an invitation locks all of its buttons.
  const busy = (id: number) => isRevoking(reissuingIds, id) || isRevoking(revokingIds, id)

  return (
    <>
      <PageHeader title="Members" description={`Manage who can access ${orgLogin} in Clevis.`} />

      {rolePending ? (
        <div className="card px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Checking your access…
        </div>
      ) : notAdmin ? (
        <div className="card px-4 py-4">
          <span className="section-title">Invitations</span>
          <p className="text-xs text-muted-foreground mt-1">
            {role === "member"
              ? `Inviting people to ${orgLogin} in Clevis is limited to organization admins. You're a member, not an admin.`
              : `Inviting people to ${orgLogin} in Clevis needs an admin membership, and you don't have one for this organization.`}
          </p>
        </div>
      ) : (
        <>
        <HygieneScoringCard orgLogin={orgLogin} />
        <ScheduledScansCard orgLogin={orgLogin} />
        <WaiversCard orgLogin={orgLogin} />
        <EvidenceExportCard orgLogin={orgLogin} />
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="card">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-title">Invite a member</span>
            </div>
            <div className="p-4 flex flex-col gap-3">
              <div>
                <label htmlFor="invite-email" className="text-xs font-medium text-foreground block mb-1.5">Email</label>
                <textarea
                  id="invite-email"
                  rows={addresses.length > 1 ? 4 : 1}
                  placeholder="teammate@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  // Enter sends a single address; with several, Enter adds a line and Ctrl/Cmd+Enter sends.
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" || (addresses.length > 1 && !e.ctrlKey && !e.metaKey)) return
                    e.preventDefault()
                    if (addresses.length > 0 && addresses.length <= MAX_BULK_INVITES && !invite.isPending) invite.mutate()
                  }}
                  className="w-full min-w-0 resize-none rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 md:text-sm dark:bg-input/30"
                />
                <p className="text-[0.6875rem] text-muted-foreground mt-1">
                  Paste several addresses (comma or one per line) to invite them all at once.
                </p>
                {addresses.length > MAX_BULK_INVITES && (
                  <p role="alert" className="text-xs text-destructive mt-1">
                    Invite up to {MAX_BULK_INVITES} addresses at a time ({addresses.length} entered).
                  </p>
                )}
              </div>
              <Button
                onClick={() => invite.mutate()}
                disabled={invite.isPending || addresses.length === 0 || addresses.length > MAX_BULK_INVITES}
              >
                <EnvelopeSimple className="size-3.5" />
                {invite.isPending ? "Inviting…" : addresses.length > 1 ? `Send ${addresses.length} invites` : "Send invite"}
              </Button>
              {invite.isError && <p className="text-xs text-destructive">{invite.error.message}</p>}
              {bulkResults && (
                <div className="text-xs bg-muted/30 border border-border/50 rounded-md p-2">
                  <p className="text-muted-foreground">
                    No email is sent automatically. Links are shown only now, so copy the ones you need before you leave this page.
                  </p>
                  <ul className="mt-2 flex flex-col gap-1.5">
                    {bulkResults.map((r) => (
                      <li key={r.email} className="flex items-center justify-between gap-2">
                        <span className="min-w-0 break-all">
                          <span className="text-foreground/80">{r.email}</span>{" "}
                          <span className={r.status === "created" ? "text-muted-foreground" : "text-yellow-400"}>
                            {BULK_STATUS_LABEL[r.status]}
                          </span>
                        </span>
                        {r.invite_link && (
                          <span className="shrink-0">
                            <CopyButton key={r.invite_link} value={r.invite_link} ariaLabel={`Copy invitation link for ${r.email}`} />
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {lastLink && (
                <div className="text-xs text-muted-foreground break-all bg-muted/30 border border-border/50 rounded-md p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span>Share this link — no email is sent automatically. It is shown only now, so copy it before you leave this page.</span>
                    {/* keyed by the link so a new invitation never inherits "Copied" from the previous one */}
                    <span className="shrink-0">
                      <CopyButton key={lastLink.link} value={lastLink.link} ariaLabel="Copy invitation link" />
                    </span>
                  </div>
                  <div className="font-mono text-foreground/80 mt-1">{lastLink.link}</div>
                </div>
              )}
            </div>
          </div>

          <div className="card lg:col-span-2">
            <div className="px-4 py-3 border-b border-border">
              <span className="section-title">Clevis workspace invitations</span>
            </div>
            {isLoading ? (
              <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
                <CircleNotch className="size-3.5 animate-spin" /> Loading…
              </div>
            ) : invitationsQuery.isError && !invitationsQuery.data ? (
              // Distinct from "No invitations yet" -- a failed load (e.g. a 403) must not read as empty.
              <SectionError
                message={`Couldn't load invitations: ${invitationsQuery.error.message}`}
                onRetry={() => invitationsQuery.refetch()}
                retrying={invitationsQuery.isFetching}
              />
            ) : invitations.length === 0 ? (
              <div className="px-4 py-8">
                <p className="text-sm text-muted-foreground">No invitations yet</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left text-muted-foreground font-medium px-4 py-2">Email</th>
                      <th className="text-left text-muted-foreground font-medium px-4 py-2">Status</th>
                      <th className="text-left text-muted-foreground font-medium px-4 py-2">Expires</th>
                      <th className="text-right text-muted-foreground font-medium px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {invitations.map((inv) => (
                      <tr key={inv.id}>
                        <td className="px-4 py-2.5 text-foreground/80">
                          {inv.email}
                          {revokeErrors[inv.id] && (
                            <p role="alert" className="mt-1 text-xs text-destructive">{revokeErrors[inv.id]}</p>
                          )}
                          {rowNotes[inv.id] && (
                            <p
                              role={rowNotes[inv.id].ok ? "status" : "alert"}
                              className={`mt-1 text-xs ${rowNotes[inv.id].ok ? "text-muted-foreground" : "text-destructive"}`}
                            >
                              {rowNotes[inv.id].text}
                            </p>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-muted-foreground">{effectiveInvitationStatus(inv, now)}</td>
                        <ExpiryCell inv={inv} now={now} />
                        <td className="px-4 py-2.5 text-right">
                          {["pending", "expired"].includes(effectiveInvitationStatus(inv, now)) && (
                            <div className="flex justify-end gap-1.5">
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => reissue.mutate({ id: inv.id, kind: "regenerate" })}
                                disabled={busy(inv.id)}
                              >
                                <ArrowsClockwise className="size-3" />
                                Regenerate link
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => reissue.mutate({ id: inv.id, kind: "resend" })}
                                disabled={busy(inv.id)}
                              >
                                <PaperPlaneTilt className="size-3" />
                                Resend
                              </Button>
                              {effectiveInvitationStatus(inv, now) === "pending" && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => revoke.mutate(inv.id)}
                                  disabled={busy(inv.id)}
                                >
                                  <X className="size-3" />
                                  Revoke
                                </Button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
        </>
      )}

      {!rolePending && !notAdmin && <BadgeSettingsCard orgLogin={orgLogin} />}

      <div className="mt-4">
        <GithubRoster orgLogin={orgLogin} canReview={role === "admin"} />
      </div>
    </>
  )
}
