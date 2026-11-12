"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { SectionError } from "@/components/section-error"
import type { DigestPreview, DigestSettings } from "@/lib/api/types"

type Choice = "follow" | "on" | "off"

const toChoice = (enabled: boolean | null): Choice => (enabled === null ? "follow" : enabled ? "on" : "off")
const fromChoice = (choice: Choice): boolean | null => (choice === "follow" ? null : choice === "on")

/** Org admins control this org's leadership digest: follow or override the instance cadence, choose who
 * receives it, preview what it says, and send themselves a test. */
export function DigestCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["digest", orgLogin]
  const query = useQuery<DigestSettings>({ queryKey, queryFn: () => api.orgs.digest(orgLogin), retry: false })
  const update = useMutation({
    mutationFn: (body: { enabled?: boolean | null; recipients?: string[] | null }) => api.orgs.setDigest(orgLogin, body),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })
  // Tagged with the org it was built for, so another org's digest (or a completion that lands after the
  // org changed) is never shown under this one.
  const [previewState, setPreviewState] = useState<{ org: string; preview: DigestPreview } | null>(null)
  const preview = previewState?.org === orgLogin ? previewState.preview : null
  const previewMutation = useMutation({
    mutationFn: (org: string) => api.orgs.digestPreview(org),
    onSuccess: (data, org) => setPreviewState({ org, preview: data }),
  })
  const testMutation = useMutation({ mutationFn: () => api.orgs.sendTestDigest(orgLogin) })

  const data = query.data
  const chosen = data?.recipients ?? null

  function toggleRecipient(address: string, on: boolean) {
    const current = new Set(chosen ?? data?.resolved_recipients ?? [])
    if (on) current.add(address)
    else current.delete(address)
    // Unchecking the last address would leave nobody; fall back to every verified admin instead.
    update.mutate({ recipients: current.size === 0 ? null : [...current].sort() })
  }

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Leadership digest</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !data ? (
        <SectionError
          message={`Couldn't load the digest settings: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-3">
          <div className="flex flex-col gap-2">
            <label htmlFor="org-digest" className="text-xs font-medium text-foreground">
              Email this organization&rsquo;s digest
            </label>
            <select
              id="org-digest"
              value={toChoice(data.enabled)}
              disabled={update.isPending}
              onChange={(e) => update.mutate({ enabled: fromChoice(e.target.value as Choice) })}
              className="text-xs card text-foreground px-2 py-1.5 max-w-72 focus:outline-none focus:ring-1 focus:ring-ring"
            >
              <option value="follow">Follow the instance setting ({data.instance_cadence})</option>
              <option value="on">Always send</option>
              <option value="off">Never send</option>
            </select>
            <p className="text-xs text-muted-foreground">
              {data.effective ? `Sent ${data.cadence}.` : "Not sent."}{" "}
              {!data.smtp_configured && "Email (SMTP) is not configured on this server, so nothing can be sent yet."}
            </p>
          </div>

          <fieldset className="flex flex-col gap-1.5" disabled={update.isPending}>
            <legend className="text-xs font-medium text-foreground mb-1">Recipients</legend>
            <p className="text-xs text-muted-foreground">
              {chosen === null ? "Every verified organization admin (default)." : "Only the people checked below."} Only
              verified members of this organization can be chosen.
            </p>
            {data.available_recipients.map((address) => (
              <label key={address} className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={data.resolved_recipients.includes(address)}
                  onChange={(e) => toggleRecipient(address, e.target.checked)}
                />
                {address}
              </label>
            ))}
            {/* A stored address that stopped being a verified member would make every later save fail
                validation, so it stays listed (checked) until the admin unchecks it. */}
            {(chosen ?? [])
              .filter((address) => !data.available_recipients.includes(address))
              .map((address) => (
                <label key={address} className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input type="checkbox" checked onChange={() => toggleRecipient(address, false)} />
                  {address} (no longer a verified member)
                </label>
              ))}
            {chosen !== null && (
              <button
                type="button"
                className="self-start text-xs text-muted-foreground hover:text-foreground"
                onClick={() => update.mutate({ recipients: null })}
              >
                Reset to every verified admin
              </button>
            )}
          </fieldset>

          <div className="flex items-center gap-2 flex-wrap">
            <Button size="sm" variant="outline" disabled={previewMutation.isPending} onClick={() => previewMutation.mutate(orgLogin)}>
              {previewMutation.isPending ? "Building…" : "Preview"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={testMutation.isPending || !data.smtp_configured}
              onClick={() => testMutation.mutate()}
            >
              {testMutation.isPending ? "Sending…" : "Send test to me"}
            </Button>
            {testMutation.isSuccess && <span className="text-xs text-accent">Sent to {testMutation.data.sent_to}.</span>}
          </div>
          {preview && (
            <div className="border border-border rounded-md p-3">
              <p className="text-xs font-medium mb-1">{preview.subject}</p>
              {preview.empty && (
                <p className="text-xs text-muted-foreground mb-1">No scan has run yet, so this digest would not be sent.</p>
              )}
              <pre className="text-xs text-muted-foreground whitespace-pre-wrap">{preview.body}</pre>
            </div>
          )}
          {(update.isError || previewMutation.isError || testMutation.isError) && (
            <p role="alert" className="text-xs text-destructive">
              {(update.error ?? previewMutation.error ?? testMutation.error)?.message}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
