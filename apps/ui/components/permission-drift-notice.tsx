"use client"

import { useEffect, useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowSquareOut, Copy, Warning } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import type { InstallationMeta } from "@/lib/api/types"
import { githubWebUrl } from "@/lib/github-web"
import { invalidateInstallations } from "@/lib/query-invalidation"

// GitHub page for this installation with the single "Accept new permissions" button
// (owner only). Needs no App slug, unlike the generic app page.
export function approvalUrl(install: InstallationMeta): string | null {
  if (install.installation_id == null) return null
  const base =
    install.account_type === "Organization"
      ? `organizations/${encodeURIComponent(install.account_login)}/settings/installations`
      : "settings/installations"
  return githubWebUrl(`${base}/${install.installation_id}/permissions/update`)
}

function useRefreshPermissions(install: InstallationMeta) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () =>
      api.installations.refreshPermissions(
        install.account_type === "Organization"
          ? { scope: "org", orgLogin: install.account_login }
          : { scope: "me" },
        install.installation_id!,
      ),
    onSuccess: () => invalidateInstallations(queryClient),
  })
}

/**
 * Prompt to approve missing GitHub App permissions that write automations need. GitHub
 * only lets an org owner approve, so this explains what is being approved, links straight
 * to the approval button, offers a message for a non-owner to forward, and re-checks when
 * the user returns. Shows a "Check now" line when permissions were never synced.
 */
export function PermissionDriftNotice({
  install,
  className,
}: {
  install: InstallationMeta
  className?: string
}) {
  const refresh = useRefreshPermissions(install)
  const [waiting, setWaiting] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const wrap = ["text-xs", className].filter(Boolean).join(" ")
  const canRefresh = install.installation_id != null
  const isOrg = install.account_type === "Organization"

  // After sending the user to GitHub, re-read permissions when they come back to this tab.
  // Skipped while a refresh is in flight so an older read can't land after a newer one.
  const { mutate, isPending } = refresh
  useEffect(() => {
    if (!waiting) return
    const onFocus = () => {
      if (!isPending) mutate()
    }
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [waiting, mutate, isPending])

  if (install.permissions_synced_at === null) {
    return (
      <div className={`${wrap} text-muted-foreground flex flex-col gap-1.5`}>
        <p className="flex items-center gap-1.5">
          <Warning className="size-3 shrink-0" />
          Permissions not yet checked for {install.account_login}.
          {canRefresh && (
            <Button size="sm" variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              {refresh.isPending ? "Checking…" : "Check now"}
            </Button>
          )}
        </p>
        {refresh.isError && <p className="text-destructive">{refresh.error.message}</p>}
      </div>
    )
  }

  const blocked = install.blocked_features ?? []
  if (blocked.length === 0) return null

  const url = approvalUrl(install)
  const items = blocked.map((f) => ({
    label: f.label,
    why: install.automations?.find((a) => a.feature === f.feature)?.description,
  }))

  const request = [
    `Hi! Clevis needs extra GitHub access for ${install.account_login}. Only a GitHub organization owner can approve it.`,
    "",
    "What it enables:",
    ...items.map((i) => `- ${i.label}${i.why ? `: ${i.why}` : ""}`),
    "",
    url ? `To approve, open ${url} and click "Accept new permissions".` : "",
  ].join("\n")

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(request)
      setCopied(true)
      setCopyFailed(false)
    } catch {
      setCopied(false)
      setCopyFailed(true)
    }
  }

  return (
    <div className={`${wrap} border border-warning/30 bg-warning/5 px-3 py-2.5 flex flex-col gap-1.5`}>
      <p className="text-warning font-medium flex items-center gap-1.5">
        <Warning className="size-3.5 shrink-0" />
        {items.length} automation{items.length === 1 ? " needs" : "s need"} extra GitHub access for {install.account_login}
      </p>
      <ul className="text-muted-foreground list-disc pl-6">
        {items.map((i) => (
          <li key={i.label}>
            <span className="text-foreground">{i.label}</span>
            {i.why && <span> — {i.why}</span>}
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground">
        1. Open GitHub. 2. Click <strong>Accept new permissions</strong>. 3. Come back here — this updates
        itself.{" "}
        {isOrg
          ? "Only a GitHub organization owner can do step 2; if that isn’t you, send them the request below."
          : "This is your own account, so you can approve it yourself."}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            onClick={() => setWaiting(true)}
            className="text-primary hover:underline inline-flex items-center gap-1 w-fit"
          >
            <ArrowSquareOut className="size-3.5" />
            Approve on GitHub
          </a>
        )}
        {isOrg && (
          <Button size="sm" variant="outline" onClick={copy}>
            <Copy className="size-3.5" />
            {copied ? "Copied" : "Copy request for owner"}
          </Button>
        )}
        {canRefresh && (
          <Button size="sm" variant="outline" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            {refresh.isPending ? "Checking…" : "I approved it — check now"}
          </Button>
        )}
      </div>
      {copyFailed && (
        <>
          <p className="text-destructive">Couldn&rsquo;t copy automatically. Select and copy this instead:</p>
          <textarea
            readOnly
            value={request}
            rows={5}
            aria-label="Request for owner"
            onFocus={(e) => e.currentTarget.select()}
            className="w-full border border-border bg-background p-2 font-mono"
          />
        </>
      )}
      {refresh.isError && <p className="text-destructive">{refresh.error.message}</p>}
    </div>
  )
}
