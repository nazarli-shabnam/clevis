"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { ArrowRight, X } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import type { ActiveScope } from "@/lib/active-scope"
import type { InstallationMeta, InvitationOut } from "@/lib/api/types"

const DISMISS_KEY = "clevis:first-run-checklist-dismissed"

function readDismissed(): boolean {
  try {
    return localStorage.getItem(DISMISS_KEY) === "1"
  } catch {
    return false
  }
}

interface Step {
  id: string
  label: string
  hint: string
  href: string
}

/**
 * Dismissible "what's left to set up" card for the Overview. Only incomplete steps are listed,
 * and the card disappears once none remain. Renders nothing until the installation lookup has
 * resolved (or if it failed), so a slow or erroring request never flashes a false to-do list.
 */
export function FirstRunChecklist({
  scope,
  hasScan,
  canInvite,
  membersUrl,
}: {
  scope: ActiveScope | null
  // `null` while the scan data (cockpit) is still loading.
  hasScan: boolean | null
  canInvite: boolean
  membersUrl: string
}) {
  const [dismissed, setDismissed] = useState(readDismissed)

  // Same key/fetcher as the Permissions page, so the two share one cached request.
  const installsQuery = useQuery<InstallationMeta[]>({
    queryKey: ["installations.permissions", scope?.kind, scope?.login],
    queryFn: () => (scope?.kind === "org" ? api.installations.listForOrg(scope.login) : api.installations.list()),
    enabled: !!scope && !dismissed,
    retry: false,
  })
  const invitesQuery = useQuery<InvitationOut[]>({
    queryKey: ["invitations", scope?.login],
    queryFn: () => api.invitations.list(scope!.login),
    enabled: !!scope && canInvite && !dismissed,
    retry: false,
  })

  if (dismissed || !scope || !installsQuery.isSuccess || hasScan === null) return null

  const installs = installsQuery.data.filter((i) => i.installation_id != null)
  const installed = installs.length > 0
  const permissionsOk = installs.every(
    (i) => i.permissions_synced_at != null && (i.blocked_features ?? []).length === 0,
  )

  const steps: Step[] = []
  if (!installed) {
    steps.push({
      id: "install",
      label: "Install the GitHub App",
      hint: "Lets Clevis read your org and run automations without a pasted token.",
      href: "/settings",
    })
  } else if (!permissionsOk) {
    steps.push({
      id: "permissions",
      label: "Grant the permissions your automations need",
      hint: "Some automations are blocked or not yet checked.",
      href: "/automation/permissions",
    })
  }
  if (!hasScan) {
    steps.push({
      id: "scan",
      label: "Run your first security scan",
      hint: "Gives the Overview a score and trend to track.",
      href: "/security",
    })
  }
  if (canInvite && invitesQuery.isSuccess && invitesQuery.data.length === 0) {
    steps.push({
      id: "invite",
      label: "Invite a teammate",
      hint: "Share the dashboard with the people who triage alerts.",
      href: membersUrl,
    })
  }

  if (steps.length === 0) return null

  return (
    <section className="card mb-6" aria-label="Getting started">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-4">
        <span className="section-label">Getting started — {steps.length} step{steps.length === 1 ? "" : "s"} left</span>
        <button
          type="button"
          aria-label="Dismiss getting started"
          className="text-muted-foreground hover:text-foreground"
          onClick={() => {
            try {
              localStorage.setItem(DISMISS_KEY, "1")
            } catch {
              // Storage blocked: the card just comes back next visit.
            }
            setDismissed(true)
          }}
        >
          <X className="size-3.5" />
        </button>
      </div>
      <ul className="divide-y divide-border">
        {steps.map((s) => (
          <li key={s.id}>
            <Link href={s.href} className="px-4 py-3 flex items-center justify-between gap-3 hover:bg-muted/30">
              <span className="flex flex-col gap-0.5">
                <span className="text-sm text-foreground">{s.label}</span>
                <span className="text-xs text-muted-foreground">{s.hint}</span>
              </span>
              <ArrowRight className="size-3.5 text-muted-foreground shrink-0" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
