"use client"

import { useQuery } from "@tanstack/react-query"
import { CircleNotch, DownloadSimple } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { SectionError } from "@/components/section-error"
import { api } from "@/lib/api/client"
import { toCsv } from "@/lib/csv"
import { downloadTextFile } from "@/lib/download"
import { relativeTime } from "@/lib/format"
import type { MemberAccess } from "@/lib/api/types"

/** One row per direct grant, or a single row when there are none, so the offboarding checklist always
 * records the person, their role/2FA and last activity. */
export function accessCsv(a: MemberAccess): string {
  const person = (grant?: MemberAccess["direct_grants"][number]) => ({
    login: a.login,
    member: a.is_member ? "yes" : "no",
    role: a.role ?? "",
    twoFactor: a.two_factor_enabled === null ? "unknown" : a.two_factor_enabled ? "yes" : "no",
    lastPush: a.last_push_at ?? "",
    repo: grant?.repo ?? "",
    permission: grant?.permission ?? "",
  })
  const rows = a.direct_grants.length ? a.direct_grants.map((g) => person(g)) : [person()]
  return toCsv(rows, [
    { header: "Login", value: (r) => r.login },
    { header: "Org member", value: (r) => r.member },
    { header: "Role", value: (r) => r.role },
    { header: "2FA", value: (r) => r.twoFactor },
    { header: "Last push", value: (r) => r.lastPush },
    { header: "Repo (direct grant)", value: (r) => r.repo },
    { header: "Permission", value: (r) => r.permission },
  ])
}

export function MemberAccessSheet({
  orgLogin,
  login,
  onClose,
}: {
  orgLogin: string
  /** The person under review; null keeps the sheet closed. */
  login: string | null
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ["collab", "member-access", orgLogin, login],
    queryFn: () => api.collab.memberAccess(orgLogin, login!),
    enabled: login !== null,
    retry: false,
  })
  const a = query.data

  return (
    <Sheet open={login !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Access review: {login}</SheetTitle>
          <SheetDescription>What Clevis knows this person can reach in {orgLogin}, before you remove them.</SheetDescription>
        </SheetHeader>

        <div className="px-4 pb-4 flex flex-col gap-4">
          {query.isLoading ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Loading…
            </p>
          ) : query.isError ? (
            <SectionError message={query.error.message} onRetry={() => query.refetch()} retrying={query.isFetching} />
          ) : a && !a.synced ? (
            <p className="text-sm text-muted-foreground">
              This needs the GitHub App installed on {orgLogin} and its first membership sync to finish. Until then Clevis has
              no roster or access data to show.
            </p>
          ) : a ? (
            <>
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                <dt className="text-muted-foreground">Org member</dt>
                <dd>{a.is_member ? "Yes" : "No (not in the org roster)"}</dd>
                <dt className="text-muted-foreground">Role</dt>
                <dd className="capitalize">{a.role ?? "—"}</dd>
                <dt className="text-muted-foreground">2FA</dt>
                <dd>{a.two_factor_enabled === null ? "Unknown" : a.two_factor_enabled ? "Enabled" : "Not enabled"}</dd>
                <dt className="text-muted-foreground">Last push</dt>
                <dd>
                  {a.last_push_at ? `${relativeTime(a.last_push_at)} in ${a.last_push_repo}` : "None recorded"}
                </dd>
                <dt className="text-muted-foreground">Last activity</dt>
                <dd>{a.last_event_at ? relativeTime(a.last_event_at) : "None recorded"}</dd>
              </dl>

              <div>
                <h3 className="section-label mb-1.5">Direct repository access ({a.direct_grants.length})</h3>
                {a.direct_grants.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No direct grants recorded.</p>
                ) : (
                  <ul className="divide-y divide-border text-xs border border-border rounded-md">
                    {a.direct_grants.map((g) => (
                      <li key={g.repo} className="flex items-center justify-between gap-2 px-3 py-2">
                        <span className="truncate">{g.repo}</span>
                        <span className="stat-chip shrink-0">
                          {g.permission}
                          {g.is_outside_collaborator ? " · outside" : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-2 text-[0.6875rem] text-muted-foreground">
                  Direct grants seen since the GitHub App was connected. Access through teams or the org&apos;s base
                  permission is not included, so this list can understate what the person can reach.
                </p>
              </div>

              <Button
                size="sm"
                variant="outline"
                className="self-start"
                onClick={() =>
                  downloadTextFile(`clevis-access-${orgLogin}-${a.login}.csv`, accessCsv(a), "text/csv")
                }
              >
                <DownloadSimple className="size-3.5" /> Export for review (CSV)
              </Button>
            </>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
