"use client"

import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SectionError } from "@/components/section-error"
import { api } from "@/lib/api/client"
import type { GithubAppManifest, GithubAppStatus } from "@/lib/api/types"

/** Hands the manifest to GitHub the way its manifest flow requires: the browser itself POSTs a form whose
 * `manifest` field is the JSON, so the admin only has to click Create on github.com. */
export function submitManifestToGithub(m: GithubAppManifest) {
  const form = document.createElement("form")
  form.method = "post"
  form.action = m.post_url
  const field = document.createElement("input")
  field.type = "hidden"
  field.name = "manifest"
  field.value = JSON.stringify(m.manifest)
  form.appendChild(field)
  document.body.appendChild(form)
  form.submit()
}

const SOURCE_TEXT: Record<GithubAppStatus["source"], string> = {
  env: "Configured through environment variables (GITHUB_APP_*). They take precedence over an App created here.",
  database: "Created from Settings; its credentials are stored encrypted.",
  none: "No GitHub App is set up yet.",
}

/** Workspace-admin card: create the GitHub App from Clevis (GitHub's manifest flow), or see exactly which
 * permissions and events to set on an App registered by hand. */
export function GithubAppSetupCard() {
  const status = useQuery<GithubAppStatus>({ queryKey: ["github-app-status"], queryFn: () => api.githubApp.status(), retry: false })
  const [organization, setOrganization] = useState("")
  const [readOnly, setReadOnly] = useState(false)
  const [confirmReplace, setConfirmReplace] = useState(false)

  const start = useMutation({
    mutationFn: () =>
      api.githubApp.manifest({
        ui_origin: window.location.origin,
        organization: organization.trim() || undefined,
        read_only: readOnly,
        replace: status.data?.source === "database" && confirmReplace,
      }),
    onSuccess: submitManifestToGithub,
  })

  const s = status.data
  const canCreate = s?.source === "none" || (s?.source === "database" && confirmReplace)

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">GitHub App</span>
        <p className="text-xs text-muted-foreground mt-0.5">
          Create the App from here instead of registering it by hand: Clevis fills in every permission, event and
          URL, and you only click Create on GitHub.
        </p>
      </div>
      {status.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : status.isError || !s ? (
        <SectionError
          message={status.error instanceof Error ? status.error.message : "Couldn't load the GitHub App status."}
          onRetry={() => status.refetch()}
          retrying={status.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-3 text-xs">
          <p role="status" className="text-muted-foreground">
            {SOURCE_TEXT[s.source]}
            {s.slug ? <> App: <strong>{s.slug}</strong>.</> : null}
          </p>

          {s.source !== "env" && (
            <div className="flex flex-col gap-2">
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="flex flex-col gap-1">
                  <span className="text-muted-foreground">Create it under an organization (optional)</span>
                  <Input
                    value={organization}
                    onChange={(e) => setOrganization(e.target.value)}
                    placeholder="your personal account"
                    aria-label="Organization to own the App"
                  />
                </label>
                <label className="flex items-start gap-2 pt-5">
                  <input type="checkbox" checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)} className="mt-0.5" />
                  <span>
                    Read-only (no automations). The default grants write access for the optional automations (file issues,
                    fix this, nudges, Dependabot triage, cache clear); this drops every write permission.
                  </span>
                </label>
              </div>
              {s.source === "database" && (
                <label className="flex items-start gap-2 text-destructive">
                  <input type="checkbox" checked={confirmReplace} onChange={(e) => setConfirmReplace(e.target.checked)} className="mt-0.5" />
                  <span>Replace the existing App. Installations of the old App stop working.</span>
                </label>
              )}
              <div>
                <Button size="sm" disabled={!canCreate || start.isPending} onClick={() => start.mutate()}>
                  {start.isPending ? "Opening GitHub…" : "Create GitHub App"}
                </Button>
              </div>
              {start.isError && (
                <p role="alert" className="text-destructive">
                  {start.error instanceof Error ? start.error.message : "Couldn't start the GitHub App setup."}
                </p>
              )}
            </div>
          )}

          <details className="text-muted-foreground">
            <summary className="cursor-pointer">Registered an App by hand? Set exactly this</summary>
            <p className="mt-2">Repository / organization permissions (generated from what Clevis uses):</p>
            <ul className="mt-1 grid gap-x-6 sm:grid-cols-2 font-mono">
              {Object.entries(s.permissions).map(([name, level]) => (
                <li key={name}>
                  {name}: {level}
                </li>
              ))}
            </ul>
            <p className="mt-2">Subscribe to events: {s.events.join(", ")}.</p>
            <p className="mt-2">
              GitHub cannot change a registered App&apos;s permissions through its API, so edit them on the App&apos;s
              settings page; an organization owner then approves the new request (Clevis already handles that).
            </p>
          </details>
        </div>
      )}
    </div>
  )
}
