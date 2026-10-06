"use client"

import { useState } from "react"
import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { ArrowRight, X } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import type { ActiveScope } from "@/lib/active-scope"
import { dismissalKey, readDismissed, writeDismissed } from "@/lib/first-run-dismissal"
import type { InstallationMeta, InvitationOut } from "@/lib/api/types"

interface Step {
  id: string
  label: string
  hint: string
  href: string
}

/**
 * Dismissible "what's left to set up" card for the Overview. Only incomplete steps are listed,
 * and the card disappears once none remain. Nothing is listed until the data behind it has loaded, so a
 * slow request never flashes a false to-do list; if a request fails, a short notice with a retry shows
 * instead of the card silently never appearing. Dismissal is remembered per user and account.
 */
export function FirstRunChecklist({
  scope,
  userId,
  hasScan,
  scanFailed = false,
  hasOrg = null,
  hasAutomationRun = null,
  canInvite,
  membersUrl,
}: {
  scope: ActiveScope | null
  userId: number | null
  // `null` while the scan data (cockpit) is still loading.
  hasScan: boolean | null
  // The scan data (cockpit) failed to load. The Overview already shows that error with its own retry,
  // so the card only explains why the checklist is missing.
  scanFailed?: boolean
  // Whether the user belongs to any Clevis org; `null` while unknown. Only `false` shows the connect step.
  hasOrg?: boolean | null
  // Whether an automation has really run; `null` when unknown (the step is then hidden, not shown).
  hasAutomationRun?: boolean | null
  canInvite: boolean
  membersUrl: string
}) {
  const key = scope && userId != null ? dismissalKey(userId, scope.kind, scope.login) : null
  // The key this tab dismissed, so the click takes effect at once; otherwise storage decides, so
  // switching user/account re-reads that account's own dismissal.
  const [dismissedKey, setDismissedKey] = useState<string | null>(null)
  const dismissed = key !== null && (dismissedKey === key || readDismissed(key))

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

  if (dismissed || !scope) return null
  if (installsQuery.isError) {
    return (
      <section className="card mb-6 px-4 py-3 flex items-center justify-between gap-4" aria-label="Getting started">
        <span className="text-sm text-muted-foreground">Couldn&apos;t load your setup progress.</span>
        <button type="button" className="text-xs text-foreground underline" onClick={() => installsQuery.refetch()}>
          Retry
        </button>
      </section>
    )
  }
  if (scanFailed) {
    return (
      <section className="card mb-6 px-4 py-3" aria-label="Getting started">
        <span className="text-sm text-muted-foreground">
          Your setup checklist will appear once the Overview data loads. Retry it from the error below.
        </span>
      </section>
    )
  }
  if (!installsQuery.isSuccess || hasScan === null) return null

  const installs = installsQuery.data.filter((i) => i.installation_id != null)
  const installed = installs.length > 0
  const permissionsOk = installs.every(
    (i) => i.permissions_synced_at != null && (i.blocked_features ?? []).length === 0,
  )

  const steps: Step[] = []
  if (hasOrg === false) {
    steps.push({
      id: "connect-org",
      label: "Connect an organization",
      hint: "Install the GitHub App on an org to share the dashboard with a team.",
      href: "/settings",
    })
  }
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
  if (hasAutomationRun === false) {
    steps.push({
      id: "automation",
      label: "Run your first automation",
      hint: "Try a bulk branch-protection apply, Dependabot triage, or a workflow dispatch.",
      href: "/automation",
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
            if (key === null) return
            writeDismissed(key)
            setDismissedKey(key)
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
