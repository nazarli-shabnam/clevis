"use client"

import { useParams } from "next/navigation"
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Loader2, Mail, X } from "lucide-react"
import { api } from "@/lib/api/client"
import { addRevokingId, isRevoking, removeRevokingId } from "@/lib/revoke-pending"
import type { InvitationOut } from "@/lib/api/types"

export default function OrgMembersPage() {
  const params = useParams<{ login: string }>()
  const orgLogin = params.login
  const queryClient = useQueryClient()
  const [email, setEmail] = useState("")
  const [lastLink, setLastLink] = useState<string | null>(null)

  const { data: invitations = [], isLoading } = useQuery<InvitationOut[]>({
    queryKey: ["invitations", orgLogin],
    queryFn: () => api.invitations.list(orgLogin),
  })

  const [revokingIds, setRevokingIds] = useState<Set<number>>(() => new Set())

  const invite = useMutation({
    mutationFn: () => api.invitations.create(orgLogin, email.trim()),
    onSuccess: (data) => {
      setLastLink(data.invite_link)
      setEmail("")
      queryClient.invalidateQueries({ queryKey: ["invitations", orgLogin] })
    },
  })

  const revoke = useMutation({
    mutationFn: (id: number) => api.invitations.revoke(orgLogin, id),
    onMutate: (id) => setRevokingIds((prev) => addRevokingId(prev, id)),
    onSettled: (_data, _error, id) => setRevokingIds((prev) => removeRevokingId(prev, id)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["invitations", orgLogin] }),
  })

  return (
    <>
      <PageHeader title="Members" description={`Manage who can access ${orgLogin} in Clevis.`} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="bg-card border border-border">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-title">Invite a member</span>
          </div>
          <div className="p-4 flex flex-col gap-3">
            <div>
              <label className="text-xs font-medium text-foreground block mb-1.5">Email</label>
              <Input
                placeholder="teammate@example.com"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && email && !invite.isPending && invite.mutate()}
              />
            </div>
            <Button onClick={() => invite.mutate()} disabled={invite.isPending || !email}>
              <Mail className="size-3.5" />
              {invite.isPending ? "Inviting…" : "Send invite"}
            </Button>
            {invite.isError && <p className="text-xs text-destructive">{invite.error.message}</p>}
            {lastLink && (
              <div className="text-xs text-muted-foreground break-all bg-muted/30 border border-border/50 rounded-md p-2">
                Share this link — no email is sent automatically:
                <div className="font-mono text-foreground/80 mt-1">{lastLink}</div>
              </div>
            )}
          </div>
        </div>

        <div className="bg-card border border-border lg:col-span-2">
          <div className="px-4 py-3 border-b border-border">
            <span className="section-title">Invitations</span>
          </div>
          {isLoading ? (
            <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading…
            </div>
          ) : invitations.length === 0 ? (
            <div className="px-4 py-8">
              <p className="text-sm text-muted-foreground font-mono">— no invitations yet</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border">
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Email</th>
                    <th className="text-left text-muted-foreground font-medium px-4 py-2">Status</th>
                    <th className="text-right text-muted-foreground font-medium px-4 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {invitations.map((inv) => (
                    <tr key={inv.id}>
                      <td className="px-4 py-2.5 text-foreground/80">{inv.email}</td>
                      <td className="px-4 py-2.5 text-muted-foreground">{inv.status}</td>
                      <td className="px-4 py-2.5 text-right">
                        {inv.status === "pending" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => revoke.mutate(inv.id)}
                            disabled={isRevoking(revokingIds, inv.id)}
                          >
                            <X className="size-3" />
                            Revoke
                          </Button>
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
  )
}
