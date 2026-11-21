"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Time } from "@/components/time"
import { CircleNotch } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import { invalidateInstallations } from "@/lib/query-invalidation"
import type { PendingInstallation } from "@/lib/api/types"

const PENDING_KEY = ["pending-installations"] as const

function PendingRow({ pending }: { pending: PendingInstallation }) {
  const queryClient = useQueryClient()
  // The same, fully checked call the Setup URL page makes: the server verifies the installation really belongs
  // to this org and that the caller is a GitHub admin of it, and refuses otherwise.
  const connect = useMutation({
    mutationFn: () =>
      api.installations.sync(
        { scope: "org", orgLogin: pending.account_login },
        { account_login: pending.account_login, account_type: "Organization", installation_id: pending.installation_id },
      ),
    onSuccess: () => {
      invalidateInstallations(queryClient)
      // Connecting creates the org membership, so the org lists need a refresh too.
      queryClient.invalidateQueries({ queryKey: ["my-orgs"] })
      return queryClient.invalidateQueries({ queryKey: PENDING_KEY })
    },
  })

  return (
    <li className="px-4 py-3 text-xs flex flex-col gap-1.5">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0">
          <span className="font-mono text-foreground">{pending.account_login}</span>{" "}
          <span className="text-muted-foreground">approved on GitHub <Time iso={pending.created_at} /></span>
        </p>
        {pending.role === "approver" && (
          <Button size="sm" disabled={connect.isPending} onClick={() => connect.mutate()}>
            {connect.isPending ? (
              <><CircleNotch className="size-3 animate-spin" />Connecting…</>
            ) : (
              "Connect to Clevis"
            )}
          </Button>
        )}
      </div>
      <p className="text-muted-foreground">
        {pending.role === "approver"
          ? "You approved this installation. Connect it so the organization shows up in Clevis."
          : "You asked for this installation and it was approved. An organization owner still has to connect it to Clevis from their Settings, and then invite you to the organization."}
      </p>
      {connect.isError && (
        <p role="alert" className="text-destructive">
          Couldn&apos;t connect it: {connect.error.message}
        </p>
      )}
    </li>
  )
}

/**
 * GitHub told Clevis that an organization installation was approved, but nobody has connected it yet. Shown to the
 * owner who approved it (with a Connect button) and to the member who asked for it (with what is still needed).
 * Renders nothing when there is no such installation, or the list can't be loaded.
 */
export function PendingInstallationsCard() {
  const query = useQuery<PendingInstallation[]>({
    queryKey: PENDING_KEY,
    queryFn: () => api.installations.pending(),
    retry: false,
  })
  if (!query.data || query.data.length === 0) return null

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Waiting to be connected</span>
      </div>
      <ul className="divide-y divide-border">
        {query.data.map((p) => (
          <PendingRow key={p.installation_id} pending={p} />
        ))}
      </ul>
    </div>
  )
}
