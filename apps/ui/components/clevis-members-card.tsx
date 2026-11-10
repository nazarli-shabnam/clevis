"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import type { OrgMembership } from "@/lib/api/types"

/** Who has Clevis access to the org. Members that mirror GitHub are read-only (the next GitHub sync would
 * undo a manual change); invite-sourced ones, e.g. contractors, can be re-roled or removed by an org admin. */
export function ClevisMembersCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["org-memberships", orgLogin]
  const query = useQuery<OrgMembership[]>({ queryKey, queryFn: () => api.orgMemberships.list(orgLogin), retry: false })
  const [removing, setRemoving] = useState<OrgMembership | null>(null)

  const setRole = useMutation({
    mutationFn: ({ userId, role }: { userId: number; role: "member" | "admin" }) =>
      api.orgMemberships.setRole(orgLogin, userId, role),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })
  const remove = useMutation({
    mutationFn: (userId: number) => api.orgMemberships.remove(orgLogin, userId),
    onSettled: () => {
      setRemoving(null)
      return queryClient.invalidateQueries({ queryKey })
    },
  })

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Clevis access</span>
      </div>
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Everyone with access to {orgLogin} in Clevis. Members managed by GitHub follow their GitHub organization role;
        invited members can be changed or removed here. An organization always keeps at least one admin.
      </p>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load members: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <ul className="divide-y divide-border">
          {query.data.map((m) => (
            <li key={m.user_id} className="px-4 py-2 flex items-center justify-between gap-3 text-xs">
              <div className="min-w-0">
                <p className="font-medium text-foreground truncate">
                  {m.name ?? m.email}
                  {m.github_login && <span className="ml-2 font-mono font-normal text-muted-foreground">@{m.github_login}</span>}
                </p>
                <p className="text-muted-foreground truncate">{m.name ? `${m.email} · ` : ""}{m.editable ? "Invited" : "Managed by GitHub"}</p>
              </div>
              {m.editable ? (
                <div className="flex shrink-0 items-center gap-2">
                  <select
                    aria-label={`Role of ${m.email}`}
                    value={m.role}
                    disabled={setRole.isPending}
                    onChange={(e) => setRole.mutate({ userId: m.user_id, role: e.target.value as "member" | "admin" })}
                    className="text-xs card text-foreground px-2 py-1 focus:outline-none focus:ring-1 focus:ring-ring"
                  >
                    <option value="member">Member</option>
                    <option value="admin">Admin</option>
                  </select>
                  <Button variant="outline" size="sm" onClick={() => setRemoving(m)} aria-label={`Remove ${m.email}`}>
                    Remove
                  </Button>
                </div>
              ) : (
                <span className="shrink-0 text-muted-foreground capitalize">{m.role}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {setRole.isError && (
        <p role="alert" className="px-4 pb-2 text-xs text-destructive">
          Couldn&apos;t change the role: {setRole.error.message}
        </p>
      )}
      {remove.isError && (
        <p role="alert" className="px-4 pb-2 text-xs text-destructive">
          Couldn&apos;t remove the member: {remove.error.message}
        </p>
      )}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={`Remove ${removing?.email ?? "member"}?`}
        description={`They lose their Clevis access to ${orgLogin} immediately. They can be invited again later.`}
        confirmLabel="Remove"
        pending={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.user_id)}
      />
    </div>
  )
}
