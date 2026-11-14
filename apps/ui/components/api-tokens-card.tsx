"use client"

import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { dueTime, relativeTime } from "@/lib/format"
import { CopyButton } from "@/components/copy-button"
import { EmptyStateAction } from "@/components/empty-state-action"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"
import type { ApiToken, ApiTokenCreated } from "@/lib/api/types"

const EXPIRY_OPTIONS: { label: string; days: number | undefined }[] = [
  { label: "Never expires", days: undefined },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
]

function statusOf(t: ApiToken): "revoked" | "expired" | "active" {
  if (t.revoked_at) return "revoked"
  if (t.expires_at && new Date(t.expires_at).getTime() <= Date.now()) return "expired"
  return "active"
}

const DAY_MS = 86_400_000
// Matches the notification feed's warning window.
const EXPIRY_WARNING_DAYS = 14

/** Whole days left (rounded up) when an active token expires within the warning window, else null. */
function daysUntilExpiry(t: ApiToken): number | null {
  if (!t.expires_at) return null
  const left = new Date(t.expires_at).getTime() - Date.now()
  return left > 0 && left <= EXPIRY_WARNING_DAYS * DAY_MS ? Math.ceil(left / DAY_MS) : null
}

/** The lifetime the replacement should get: the original's span, or never for a token that never expires. */
function lifetimeDays(t: ApiToken): number | undefined {
  if (!t.expires_at) return undefined
  return Math.max(1, Math.round((new Date(t.expires_at).getTime() - new Date(t.created_at).getTime()) / DAY_MS))
}

/** One row owns its revoke mutation, so a slow or failing revoke never disables or flags another token. */
function TokenRow({
  orgLogin,
  token,
  linked,
  rotating,
  rotateDisabled,
  onRotate,
  onRevoked,
}: {
  orgLogin: string
  token: ApiToken
  linked: boolean
  rotating: boolean
  rotateDisabled: boolean
  onRotate: () => void
  onRevoked: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const revoke = useMutation({
    mutationFn: () => api.apiTokens.revoke(orgLogin, token.id),
    onSuccess: () => {
      setConfirming(false)
      onRevoked()
    },
    onError: () => setConfirming(false),
  })
  const status = statusOf(token)
  const expiresInDays = status === "active" ? daysUntilExpiry(token) : null

  return (
    <li
      id={`api-token-${token.id}`}
      aria-current={linked ? "true" : undefined}
      className={`px-4 py-2 flex items-start justify-between gap-3 text-xs ${linked ? "bg-elevated outline outline-1 outline-primary" : ""}`}
    >
      <div className="min-w-0">
        <p className="font-medium text-foreground">
          {token.name}
          <code className="ml-2 font-mono text-muted-foreground font-normal">{token.prefix}…</code>
          {status !== "active" && <span className="ml-2 text-destructive font-normal">{status}</span>}
          {expiresInDays !== null && (
            <span className="ml-2 text-warning font-normal">
              expires in {expiresInDays} day{expiresInDays === 1 ? "" : "s"}
            </span>
          )}
        </p>
        <p className="text-muted-foreground">
          scope {token.scope} · created {relativeTime(token.created_at)} by {token.created_by} ·{" "}
          {token.last_used_at ? `last used ${relativeTime(token.last_used_at)}` : "never used"}
          {status === "active" && token.expires_at ? ` · expires ${dueTime(token.expires_at)}` : ""}
        </p>
        {revoke.isError && (
          <p role="alert" className="text-destructive">
            Couldn&apos;t revoke: {revoke.error.message}
          </p>
        )}
      </div>
      {status === "active" && (
        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="outline"
            size="sm"
            disabled={rotateDisabled || rotating}
            onClick={onRotate}
            aria-label={`Rotate token ${token.name}`}
          >
            {rotating ? "Rotating…" : "Rotate"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={revoke.isPending}
            onClick={() => setConfirming(true)}
            aria-label={`Revoke token ${token.name}`}
          >
            Revoke
          </Button>
          <ConfirmDialog
            open={confirming}
            onOpenChange={setConfirming}
            title={`Revoke "${token.name}"?`}
            description="Anything using this token (for example a CI job) stops working immediately. This can't be undone."
            confirmLabel="Revoke"
            pending={revoke.isPending}
            onConfirm={() => revoke.mutate()}
          />
        </div>
      )}
    </li>
  )
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080"

// The Action rejects a non-local http:// api-url (actions/clevis-score/check.py), so a copied snippet would fail.
const API_BASE_FAILS_ACTION = !/^https:\/\//i.test(API_BASE) && !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])([:/]|$)/i.test(API_BASE)

/** A workflow step using the `clevis-score` Action with this org and API filled in. The token itself is never
 * put in the snippet: it belongs in a repository secret. */
function ciSnippet(orgLogin: string): string {
  return [
    "- uses: nazarli-shabnam/clevis/actions/clevis-score@main   # pin a tag or SHA in real use",
    "  with:",
    `    api-url: ${API_BASE}`,
    `    org: ${orgLogin}`,
    "    token: ${{ secrets.CLEVIS_API_TOKEN }}",
    "    threshold: 80",
  ].join("\n")
}

/** Org admins create, view and revoke the machine tokens CI uses (for example the `clevis-score` Action). */
export function ApiTokensCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["api-tokens", orgLogin]
  const query = useQuery<ApiToken[]>({ queryKey, queryFn: () => api.apiTokens.list(orgLogin), retry: false })

  const [name, setName] = useState("")
  const [expiry, setExpiry] = useState(0)
  // Held only in component state: the API never returns the secret again.
  const [created, setCreated] = useState<ApiTokenCreated | null>(null)
  // Set when `created` is a rotation's replacement: the old token stays valid until the admin revokes it,
  // so CI keeps working while its secret is swapped.
  const [rotatedFrom, setRotatedFrom] = useState<ApiToken | null>(null)
  const [confirmingOld, setConfirmingOld] = useState(false)

  // A notification links here with ?token=<id>: that row is marked and scrolled into view.
  const linkedToken = Number(useSearchParams()?.get("token")) || null
  const linkedLoaded = linkedToken !== null && !!query.data?.some((t) => t.id === linkedToken)
  useEffect(() => {
    if (linkedLoaded) document.getElementById(`api-token-${linkedToken}`)?.scrollIntoView?.({ block: "center" })
  }, [linkedToken, linkedLoaded])

  const create = useMutation({
    mutationFn: () => api.apiTokens.create(orgLogin, name.trim(), EXPIRY_OPTIONS[expiry].days),
    onSuccess: (data) => {
      setCreated(data)
      setName("")
      return queryClient.invalidateQueries({ queryKey })
    },
  })
  const rotate = useMutation({
    mutationFn: (old: ApiToken) => api.apiTokens.create(orgLogin, old.name, lifetimeDays(old)),
    onSuccess: (data, old) => {
      setCreated(data)
      setRotatedFrom(old)
      return queryClient.invalidateQueries({ queryKey })
    },
  })
  const revokeOld = useMutation({
    mutationFn: (old: ApiToken) => api.apiTokens.revoke(orgLogin, old.id),
    onSuccess: () => setRotatedFrom(null),
    onSettled: () => {
      setConfirmingOld(false)
      return queryClient.invalidateQueries({ queryKey })
    },
  })
  const canSubmit = name.trim() !== "" && !create.isPending && !created

  return (
    <div className="card mt-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">API tokens</span>
      </div>
      <p className="px-4 pt-3 text-xs text-muted-foreground">
        Tokens let CI read this org&apos;s latest score and trigger a scan (for example with the{" "}
        <code className="font-mono">clevis-score</code> GitHub Action). They cannot change configuration. The secret is
        shown once, when you create the token. Every change is written to the audit log.
      </p>

      {created && (
        <div role="status" className="m-4 rounded-md border border-border bg-muted/30 p-3 flex flex-col gap-2">
          <p className="text-xs font-medium text-foreground">
            Copy the token for &quot;{created.name}&quot; now. It won&apos;t be shown again.
          </p>
          <code className="block text-xs font-mono break-all bg-background border border-border/50 rounded-md p-2">
            {created.token}
          </code>
          <p className="text-xs text-muted-foreground mt-1">
            Save it as a repository secret named <code className="font-mono">CLEVIS_API_TOKEN</code>, then gate a
            workflow on the score:
          </p>
          <pre className="text-xs font-mono whitespace-pre-wrap break-all bg-background border border-border/50 rounded-md p-2">
            {ciSnippet(orgLogin)}
          </pre>
          {API_BASE_FAILS_ACTION && (
            <p role="alert" className="text-xs text-destructive">
              This API address is not HTTPS. The Clevis score action requires an HTTPS <code className="font-mono">api-url</code>,
              so replace it in the snippet before using it.
            </p>
          )}
          <div className="flex items-center gap-2">
            <CopyButton value={ciSnippet(orgLogin)} ariaLabel="Copy workflow snippet" />
          </div>
          <div className="flex items-center gap-2">
            <CopyButton value={created.token} ariaLabel={`Copy token ${created.name}`} />
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setCreated(null)
                setRotatedFrom(null)
              }}
            >
              I&apos;ve saved it
            </Button>
          </div>
          {rotatedFrom && (
            <div className="flex flex-col gap-1.5 border-t border-border/50 pt-2">
              <p className="text-xs text-muted-foreground">
                The old token &quot;{rotatedFrom.name}&quot; ({rotatedFrom.prefix}…) still works, so nothing breaks while you
                update CI. Once CI uses the new secret, revoke the old one.
              </p>
              <div>
                <Button variant="outline" size="sm" disabled={revokeOld.isPending} onClick={() => setConfirmingOld(true)}>
                  Revoke old token
                </Button>
              </div>
              {revokeOld.isError && (
                <p role="alert" className="text-xs text-destructive">
                  Couldn&apos;t revoke the old token: {revokeOld.error.message}
                </p>
              )}
              <ConfirmDialog
                open={confirmingOld}
                onOpenChange={setConfirmingOld}
                title={`Revoke "${rotatedFrom.name}" (${rotatedFrom.prefix}…)?`}
                description="Anything still using the old token (for example a CI job) stops working immediately. This can't be undone."
                confirmLabel="Revoke"
                pending={revokeOld.isPending}
                onConfirm={() => revokeOld.mutate(rotatedFrom)}
              />
            </div>
          )}
        </div>
      )}

      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load API tokens: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : query.data.length === 0 ? (
        <EmptyStateAction
          message="No API tokens yet. A token lets a CI job fail a build when this org's security score drops."
          actionLabel="Create a token"
          focusId="api-token-name"
        />
      ) : (
        <>
        {rotate.isError && (
          <p role="alert" className="px-4 pt-3 text-xs text-destructive">
            Couldn&apos;t rotate the token: {rotate.error.message}
          </p>
        )}
        <ul className="divide-y divide-border">
          {query.data.map((t) => (
            <TokenRow
              key={t.id}
              orgLogin={orgLogin}
              token={t}
              linked={t.id === linkedToken}
              // Rotating while a secret is on screen would replace it before it was copied.
              rotateDisabled={created !== null || rotate.isPending}
              rotating={rotate.isPending && rotate.variables?.id === t.id}
              onRotate={() => rotate.mutate(t)}
              onRevoked={() => queryClient.invalidateQueries({ queryKey })}
            />
          ))}
        </ul>
        </>
      )}

      <form
        className="border-t border-border p-4 grid gap-3 sm:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) create.mutate()
        }}
      >
        <div>
          <label htmlFor="api-token-name" className="text-xs font-medium block mb-1.5">Name</label>
          <Input
            id="api-token-name"
            placeholder="e.g. GitHub Actions"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="api-token-expiry" className="text-xs font-medium block mb-1.5">Expires</label>
          <select
            id="api-token-expiry"
            value={expiry}
            onChange={(e) => setExpiry(Number(e.target.value))}
            className="text-xs card text-foreground px-2 py-1.5 w-full focus:outline-none focus:ring-1 focus:ring-ring"
          >
            {EXPIRY_OPTIONS.map((o, i) => (
              <option key={o.label} value={i}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="flex items-end">
          <Button type="submit" disabled={!canSubmit}>
            {create.isPending ? "Creating…" : "Create token"}
          </Button>
        </div>
        {create.isError && (
          <p role="alert" className="sm:col-span-3 text-xs text-destructive">
            Couldn&apos;t create the token: {create.error.message}
          </p>
        )}
      </form>
    </div>
  )
}
