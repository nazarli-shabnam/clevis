"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import { Trash, Plus, CircleNotch, Check, ArrowSquareOut, CheckCircle } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel, FieldDescription } from "@/components/ui/field"
import { SectionError } from "@/components/section-error"
import { EmptyStatePage } from "@/components/empty-state"
import { PermissionDriftNotice } from "@/components/permission-drift-notice"
import { PendingInstallationsCard } from "@/components/pending-installations-card"
import { GithubAppSetupCard } from "@/components/github-app-setup-card"
import { GithubLinkField, githubLinkErrorMessage } from "@/components/github-link-field"
import { SessionsCard } from "@/components/sessions-card"
import { ChangePasswordCard } from "@/components/change-password-card"
import { ChangeEmailCard, DeleteAccountCard } from "@/components/account-cards"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { toast } from "@/components/ui/toast"
import Link from "next/link"
import { api } from "@/lib/api/client"
import { initialConfigValues, mergeSavedConfigValue } from "@/lib/config-values"
import { CONFIG_FIELDS, CONFIG_GROUPS, limitsText, validateConfigValue, type ConfigField } from "@/lib/config-fields"
import { SectionAnchor, SectionNav } from "@/components/section-nav"
import { useAuth } from "@/lib/auth-context"
import { THEMES, useTheme } from "@/lib/theme"
import type { InstallationMeta, MyOrgMembership, SavedTokenMeta } from "@/lib/api/types"
import { githubWebUrl } from "@/lib/github-web"
import { useGithubAppSlug } from "@/lib/use-github-app-slug"
import { invalidateInstallations, invalidateTokens } from "@/lib/query-invalidation"


function ProfileSection() {
  const { user, updateUser, logout } = useAuth()
  const [name, setName] = useState(user?.name || "")
  const [saved, setSaved] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const savedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const [revokeArmed, setRevokeArmed] = useState(false)

  const revokeSessions = useMutation({
    mutationFn: () => api.auth.revokeSessions(),
    // Bumping token_version invalidates this device's token too, so log out locally.
    onSuccess: () => logout(),
    // Back to the first click so the admin can retry; the error is shown below the button.
    onError: () => setRevokeArmed(false),
  })

  useEffect(() => () => clearTimeout(savedTimer.current), [])

  // Auto-disarm if not confirmed within a few seconds (same as the cache-clear confirm).
  useEffect(() => {
    if (!revokeArmed) return
    const timer = setTimeout(() => setRevokeArmed(false), 4000)
    return () => clearTimeout(timer)
  }, [revokeArmed])

  useEffect(() => {
    setName(user?.name || "")
  }, [user?.name])

  async function save() {
    setIsSaving(true)
    setSaveError(null)
    // A previous "Saved" must not linger beside (or hide) the outcome of this attempt.
    clearTimeout(savedTimer.current)
    setSaved(false)
    try {
      if (name.trim() !== (user?.name || "")) {
        const updated = await api.auth.patchMe(name.trim())
        updateUser({ name: updated.name })
      }
      setSaved(true)
      savedTimer.current = setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Couldn't save your profile.")
    } finally {
      setIsSaving(false)
    }
  }

  let buttonContent: React.ReactNode = "Save profile"
  if (saved) {
    buttonContent = <><Check className="size-3.5" />Saved</>
  } else if (isSaving) {
    buttonContent = <><CircleNotch className="size-3.5 animate-spin" />Saving…</>
  }

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Profile</span>
      </div>
      <div className="p-4 flex flex-col gap-3 max-w-sm">
        <Field>
          <FieldLabel>Display name</FieldLabel>
          <Input
            placeholder="Your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field>
          <FieldLabel>Email</FieldLabel>
          <Input value={user?.email || ""} disabled className="opacity-60 cursor-not-allowed" />
          <FieldDescription>To change it, use the Email address section below.</FieldDescription>
        </Field>
        <GithubLinkField />
        <p className="text-xs text-muted-foreground -mt-1">
          Switch between your organizations and personal GitHub account from the profile menu in the sidebar.
        </p>
        <Button onClick={save} disabled={isSaving} className="mt-1 w-fit">
          {buttonContent}
        </Button>
        {saveError && (
          <p role="alert" className="text-xs text-destructive">
            {saveError}
          </p>
        )}
      </div>
      <div className="px-4 py-3 border-t border-border flex flex-col gap-2 items-start">
        <p className="text-xs text-muted-foreground max-w-sm">
          Invalidates every session on every device, including this one — signs you out
          everywhere. Use this if a device was lost or a session token may have leaked.
        </p>
        <Button
          variant="destructive"
          onClick={() => {
            if (revokeArmed) {
              setRevokeArmed(false)
              revokeSessions.mutate()
            } else {
              setRevokeArmed(true)
            }
          }}
          disabled={revokeSessions.isPending}
        >
          {revokeSessions.isPending ? (
            <><CircleNotch className="size-3.5 animate-spin" />Signing out…</>
          ) : revokeArmed ? (
            "Click again to confirm"
          ) : (
            "Sign out of all devices"
          )}
        </Button>
        {revokeSessions.isError && (
          <p role="alert" className="text-xs text-destructive">
            Your sessions were not revoked.{" "}
            {revokeSessions.error instanceof Error ? revokeSessions.error.message : "Please try again."}
          </p>
        )}
      </div>
    </div>
  )
}


// Miniature app (sidebar, card, button) painted with the real tokens of one resolved theme.
function ThemeMiniPreview({ theme }: { theme: "dark" | "light" }) {
  return (
    <span data-theme={theme} aria-hidden className="absolute inset-0 flex bg-background">
      <span className="w-1/4 border-r border-sidebar-border bg-sidebar" />
      <span className="flex flex-1 flex-col gap-1.5 p-2">
        <span className="h-1.5 w-1/2 rounded-sm bg-foreground/80" />
        <span className="h-6 rounded-sm border border-border bg-card" />
        <span className="h-2 w-1/3 rounded-sm bg-primary" />
      </span>
    </span>
  )
}

function AppearanceSection() {
  const { theme, setTheme } = useTheme()

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Appearance</span>
        <p className="text-xs text-muted-foreground mt-0.5">
          System follows your device. The choice is saved to this browser.
        </p>
      </div>
      <div className="p-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
        {THEMES.map((t) => {
          const active = theme === t.name
          return (
            <button
              key={t.name}
              onClick={() => setTheme(t.name)}
              aria-pressed={active}
              className={[
                "flex flex-col gap-2 p-2 border rounded-md text-left transition-colors",
                active ? "border-primary bg-primary/10" : "border-border hover:bg-elevated",
              ].join(" ")}
            >
              <span className="relative block h-20 overflow-hidden rounded-sm border border-border">
                {t.name === "system" ? (
                  <>
                    <ThemeMiniPreview theme="dark" />
                    <span className="absolute inset-0" style={{ clipPath: "polygon(100% 0, 100% 100%, 0 100%)" }}>
                      <ThemeMiniPreview theme="light" />
                    </span>
                  </>
                ) : (
                  <ThemeMiniPreview theme={t.name} />
                )}
              </span>
              <span className="flex items-center gap-2 px-0.5">
                <span className="flex-1">
                  <span className="block text-xs font-medium text-foreground">{t.label}</span>
                  <span className="block text-xs text-muted-foreground">{t.description}</span>
                </span>
                {active && <Check className="size-3.5 text-primary shrink-0" />}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}


// Shared by OrgMembershipsSection and ConnectedOrgsSection: one queryFn reference so
// React Query's dedup doesn't depend on which mounts first.
function useMyOrgMemberships() {
  return useQuery<MyOrgMembership[]>({
    queryKey: ["my-orgs"],
    queryFn: () => api.orgs.mine(),
  })
}

function OrgMembershipsSection() {
  const queryClient = useQueryClient()
  const { data: memberships = [], isLoading, isError, error, isFetching, refetch } = useMyOrgMemberships()
  const [leaving, setLeaving] = useState<MyOrgMembership | null>(null)
  const leave = useMutation({
    mutationFn: (orgLogin: string) => api.orgMemberships.leave(orgLogin),
    onSuccess: () => {
      toast.success("You left the organization")
      return queryClient.invalidateQueries({ queryKey: ["my-orgs"] })
    },
    onSettled: () => setLeaving(null),
  })

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">Your organizations</span>
        {memberships.length > 0 && <span className="stat-chip">{memberships.length}</span>}
      </div>

      {isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : isError ? (
        <SectionError
          message={error instanceof Error ? error.message : "Failed to load organizations."}
          onRetry={() => refetch()}
          retrying={isFetching}
        />
      ) : memberships.length === 0 ? (
        <div className="px-4 py-6">
          <p className="text-sm text-muted-foreground">
            You&rsquo;re not a member of any organization yet. Sign in as a GitHub org admin to connect one, or
            accept an invite link from an org admin.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Organization</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Role</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {memberships.map((m) => (
                <tr key={m.org_login} className="hover:bg-elevated transition-colors">
                  <td className="px-4 py-2.5 font-mono text-foreground/80">{m.org_login}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{m.role}</td>
                  <td className="px-4 py-2.5 text-right">
                    <span className="inline-flex items-center gap-3">
                      <Link
                        href={`/settings/org/${encodeURIComponent(m.org_login)}/members`}
                        className="text-xs text-primary hover:underline"
                      >
                        {m.role === "admin" ? "Manage members" : "View members"}
                      </Link>
                      {m.role === "admin" && (
                        <Link
                          href={`/settings/org/${encodeURIComponent(m.org_login)}/settings`}
                          className="text-xs text-primary hover:underline"
                        >
                          Org settings
                        </Link>
                      )}
                      {m.role === "admin" && (
                        <Link
                          href={`/settings/org/${encodeURIComponent(m.org_login)}/activity`}
                          className="text-xs text-primary hover:underline"
                        >
                          Activity log
                        </Link>
                      )}
                      {/* Only a membership granted by an invitation can be left; a GitHub one follows the GitHub org. */}
                      {m.source === "invite" && (
                        <button
                          type="button"
                          onClick={() => setLeaving(m)}
                          disabled={leave.isPending}
                          className="text-xs text-destructive hover:underline"
                        >
                          Leave
                        </button>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {leave.isError && (
        <p role="alert" className="px-4 pb-3 text-xs text-destructive">Couldn&apos;t leave: {leave.error.message}</p>
      )}
      <ConfirmDialog
        open={leaving !== null}
        onOpenChange={(open) => !open && setLeaving(null)}
        title={`Leave ${leaving?.org_login ?? ""}?`}
        description="You lose access to this organization in Clevis. An admin can invite you again."
        confirmLabel="Leave"
        pending={leave.isPending}
        onConfirm={() => leaving && leave.mutate(leaving.org_login)}
      />
    </div>
  )
}


type ConnectedInstallation = InstallationMeta & (
  | { scope: "me" }
  | { scope: "org"; orgLogin: string }
)

function ConnectedOrgsSection() {
  const queryClient = useQueryClient()
  const [confirmRow, setConfirmRow] = useState<ConnectedInstallation | null>(null)

  const personalQuery = useQuery<InstallationMeta[]>({
    queryKey: ["installations"],
    queryFn: () => api.installations.list(),
  })
  const membershipsQuery = useMyOrgMemberships()
  const adminOrgLogins = (membershipsQuery.data ?? []).filter((m) => m.role === "admin").map((m) => m.org_login)

  // Only orgs the caller admins -- listForOrg is 403 for a plain member.
  const orgInstallQueries = useQueries({
    queries: adminOrgLogins.map((orgLogin) => ({
      queryKey: ["installations.org", orgLogin],
      queryFn: () => api.installations.listForOrg(orgLogin),
      enabled: !membershipsQuery.isLoading,
    })),
  })

  const isLoading = personalQuery.isLoading || membershipsQuery.isLoading
  const isError = personalQuery.isError || membershipsQuery.isError || orgInstallQueries.some((q) => q.isError)
  const isFetching = personalQuery.isFetching || membershipsQuery.isFetching || orgInstallQueries.some((q) => q.isFetching)
  const refetchAll = () => {
    personalQuery.refetch()
    membershipsQuery.refetch()
    orgInstallQueries.forEach((q) => q.refetch())
  }

  const rows: ConnectedInstallation[] = [
    ...(personalQuery.data ?? []).map((i) => ({ ...i, scope: "me" as const })),
    ...adminOrgLogins.flatMap((orgLogin, idx) =>
      (orgInstallQueries[idx]?.data ?? []).map((i) => ({ ...i, scope: "org" as const, orgLogin })),
    ),
  ]

  const disconnect = useMutation({
    mutationFn: (row: ConnectedInstallation) => {
      if (row.installation_id == null) return Promise.reject(new Error("This connection has no GitHub installation to disconnect."))
      return api.installations.remove(row.scope === "me" ? { scope: "me" } : { scope: "org", orgLogin: row.orgLogin }, row.installation_id)
    },
    onSuccess: (_data, row) => {
      invalidateInstallations(queryClient)
      setConfirmRow(null)
      toast.success(`Disconnected ${row.account_login}.`)
    },
    onError: (error) => {
      setConfirmRow(null)
      toast.error(error instanceof Error ? error.message : "Disconnect failed.")
    },
  })

  const rowKey = (row: ConnectedInstallation) => `${row.scope}:${row.id}`
  const needsPermissionCheck = (row: ConnectedInstallation) =>
    (row.blocked_features?.length ?? 0) > 0 || row.permissions_synced_at === null

  const { slug, appConfigured } = useGithubAppSlug()
  const installUrl = slug ? githubWebUrl(`apps/${slug}/installations/new`) : null

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">Connected GitHub accounts</span>
        {rows.length > 0 && <span className="stat-chip">{rows.length} connected</span>}
      </div>

      {disconnect.isError && (
        <p role="alert" className="px-4 pt-3 text-xs text-destructive">
          {disconnect.error instanceof Error ? disconnect.error.message : "Disconnect failed."}
        </p>
      )}

      {isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : isError ? (
        <SectionError
          message="Failed to load connected accounts."
          onRetry={refetchAll}
          retrying={isFetching}
        />
      ) : rows.length === 0 ? (
        <EmptyStatePage message="No accounts connected yet. Install the Clevis GitHub App on an organization or your personal account to get started." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Account</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Type</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Connected</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => {
                const key = rowKey(row)
                const isThisRowMutating = disconnect.isPending && disconnect.variables && rowKey(disconnect.variables) === key
                return (
                  <tr key={key} className="hover:bg-elevated transition-colors">
                    <td className="px-4 py-2.5 font-mono text-foreground/80">
                      {row.account_login}
                      {row.scope === "org" && <span className="ml-1.5 text-muted-foreground">(org)</span>}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">{row.account_type}</td>
                    <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">
                      {new Date(row.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                    </td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={disconnect.isPending}
                        onClick={() => setConfirmRow(row)}
                      >
                        {isThisRowMutating ? (
                          <CircleNotch className="size-3 animate-spin" />
                        ) : (
                          <><Trash className="size-3" />Disconnect</>
                        )}
                      </Button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Outside the scrolling table so the notice wraps to the card width on narrow screens. */}
      {rows.some(needsPermissionCheck) && (
        <div className="px-4 py-3 space-y-2 border-t border-border">
          {rows.filter(needsPermissionCheck).map((row) => (
            <PermissionDriftNotice key={rowKey(row)} install={row} />
          ))}
        </div>
      )}

      <div className="border-t border-border p-4">
        {installUrl ? (
          <Button onClick={() => { window.location.href = installUrl }}>
            <ArrowSquareOut className="size-3.5" />
            {rows.length > 0 ? "Install on another account or org" : "Install GitHub App"}
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">
            {appConfigured
              ? "The API has the GitHub App's credentials but could not determine its slug — ask a workspace admin to set GITHUB_APP_SLUG on the API."
              : "GitHub App integration isn’t set up on this instance yet — the API has no GITHUB_APP_ID / GITHUB_APP_PRIVATE_KEY. Ask a workspace admin to configure it."}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={confirmRow !== null}
        onOpenChange={(open) => { if (!open) setConfirmRow(null) }}
        title="Disconnect this account?"
        description={
          confirmRow
            ? `Clevis will stop being able to access ${confirmRow.account_login} until it's reinstalled.`
            : ""
        }
        confirmLabel="Disconnect"
        onConfirm={() => confirmRow && disconnect.mutate(confirmRow)}
        pending={disconnect.isPending}
      />
    </div>
  )
}

// Saved tokens (legacy — being replaced by the GitHub App)

function SavedTokensSection() {
  const qc = useQueryClient()
  const [addOrg, setAddOrg] = useState("")
  const [addToken, setAddToken] = useState("")
  const [addLabel, setAddLabel] = useState("")

  const { data: tokens = [], isLoading, isError, error, isFetching, refetch } = useQuery<SavedTokenMeta[]>({
    queryKey: ["tokens"],
    queryFn: () => api.tokens.list(),
  })

  const upsert = useMutation({
    mutationFn: () => api.tokens.upsert(addOrg.trim(), addToken.trim(), addLabel.trim() || undefined),
    onSuccess: () => {
      invalidateTokens(qc)
      setAddOrg("")
      setAddToken("")
      setAddLabel("")
    },
  })

  // useMutation keeps only the latest call's pending/error state, so deletes are tracked per org:
  // one row's request can't lock the others, and each failure is reported on its own row.
  const [deletingOrgs, setDeletingOrgs] = useState<Set<string>>(() => new Set())
  const [deleteErrors, setDeleteErrors] = useState<Record<string, string>>({})
  // The saved secret cannot be recovered once deleted, so the row's trash button asks first.
  const [confirmDeleteOrg, setConfirmDeleteOrg] = useState<string | null>(null)

  const remove = useMutation({
    mutationFn: (org: string) => api.tokens.delete(org),
    onMutate: (org) => {
      setDeleteErrors(({ [org]: _cleared, ...rest }) => rest)
      setDeletingOrgs((prev) => new Set(prev).add(org))
    },
    onError: (error, org) =>
      setDeleteErrors((prev) => ({ ...prev, [org]: error instanceof Error ? error.message : "Delete failed." })),
    onSettled: (_data, _error, org) =>
      setDeletingOrgs((prev) => {
        const next = new Set(prev)
        next.delete(org)
        return next
      }),
    onSuccess: () => invalidateTokens(qc),
  })

  const canAdd = addOrg.trim().length > 0 && addToken.trim().length > 0

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center justify-between">
          <span className="section-label">Personal access tokens (legacy)</span>
          {tokens.length > 0 && (
            <span className="stat-chip">{tokens.length} saved</span>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Being replaced by the GitHub App. Still used by Health &amp; Security and Cache pages until they move to the App.
          A classic token needs the <code>repo</code> scope.
        </p>
      </div>

      {isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : isError ? (
        <SectionError
          message={error instanceof Error ? error.message : "Failed to load tokens."}
          onRetry={() => refetch()}
          retrying={isFetching}
        />
      ) : tokens.length === 0 ? (
        <div className="px-4 py-6">
          <p className="text-sm text-muted-foreground">No saved tokens yet. Add one below.</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Org</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Label</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Saved</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {tokens.map((t: SavedTokenMeta) => (
                <tr key={t.org} className="hover:bg-elevated transition-colors">
                  <td className="px-4 py-2.5 font-mono text-foreground/80">
                    {t.org}
                    {deleteErrors[t.org] && (
                      <p role="alert" className="mt-1 font-sans text-xs text-destructive">
                        {deleteErrors[t.org]}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">{t.label ?? "—"}</td>
                  <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">
                    {new Date(t.created_at).toLocaleDateString(undefined, {
                      month: "short", day: "numeric", year: "numeric",
                    })}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => setConfirmDeleteOrg(t.org)}
                      disabled={deletingOrgs.has(t.org)}
                      className="text-muted-foreground hover:text-destructive transition-colors"
                      aria-label={`Delete token for ${t.org}`}
                    >
                      <Trash className="size-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={confirmDeleteOrg !== null}
        onOpenChange={(open) => { if (!open) setConfirmDeleteOrg(null) }}
        title="Delete this saved token?"
        description={
          confirmDeleteOrg
            ? `The saved token for ${confirmDeleteOrg} is removed and cannot be recovered. Anything that relies on it stops working until you add a token again.`
            : ""
        }
        confirmLabel="Delete token"
        onConfirm={() => {
          if (confirmDeleteOrg) remove.mutate(confirmDeleteOrg)
          setConfirmDeleteOrg(null)
        }}
      />

      <div className="border-t border-border p-4">
        <p className="text-xs font-medium text-foreground mb-3">Add token</p>
        <div className="grid gap-2 sm:grid-cols-3">
          <Field>
            <FieldLabel className="sr-only">Org or owner</FieldLabel>
            <Input
              placeholder="Org or owner"
              value={addOrg}
              onChange={(e) => setAddOrg(e.target.value)}
              autoComplete="off"
              data-1p-ignore
              data-lpignore="true"
            />
          </Field>
          <Field>
            <FieldLabel className="sr-only">Token</FieldLabel>
            <Input
              placeholder="ghp_… token"
              type="password"
              value={addToken}
              onChange={(e) => setAddToken(e.target.value)}
              className="font-mono"
              autoComplete="new-password"
              data-1p-ignore
              data-lpignore="true"
            />
          </Field>
          <Field>
            <FieldLabel className="sr-only">Label (optional)</FieldLabel>
            <Input
              placeholder="Label (optional)"
              value={addLabel}
              onChange={(e) => setAddLabel(e.target.value)}
            />
          </Field>
        </div>
        <Button
          onClick={() => upsert.mutate()}
          disabled={!canAdd || upsert.isPending}
          className="mt-2"
        >
          {upsert.isPending ? (
            <><CircleNotch className="size-3.5 animate-spin" />Saving…</>
          ) : (
            <><Plus className="size-3.5" />Save token</>
          )}
        </Button>
        {upsert.isError && (
          <p className="text-xs text-destructive mt-2">{upsert.error.message}</p>
        )}
      </div>
    </div>
  )
}


const withoutKey = <T,>(record: Record<string, T>, key: string): Record<string, T> =>
  Object.fromEntries(Object.entries(record).filter(([k]) => k !== key))

function InstanceConfigSection() {
  const { data: config, isLoading, isError, error, isFetching, refetch } = useQuery<Record<string, string>>({
    queryKey: ["config"],
    queryFn: api.config.getAll,
  })
  const qc = useQueryClient()
  const [saving, setSaving] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  // Keys whose last save succeeded and that have not been edited since ("Saved" beside each one's button).
  const [justSaved, setJustSaved] = useState<Record<string, true>>({})

  useEffect(() => {
    if (config) setValues((prev) => initialConfigValues(prev, config))
  }, [config])

  // The value the field currently shows: the loaded/edited value, else the key's documented default.
  // Display and Save both use it, so what Save writes is always what the admin sees.
  function visibleValue(field: ConfigField): string {
    return values[field.key] ?? field.defaultValue ?? field.options?.[0]?.value ?? ""
  }

  // What the server has stored for the key, if anything. Save is only useful when the shown value differs from
  // it; a key never stored stays savable so an admin can pin the default it displays.
  function isUnchanged(field: ConfigField, shown: string): boolean {
    const stored = config?.[field.key]
    return stored !== undefined && stored === shown
  }

  function edit(key: string, value: string) {
    setValues((v) => ({ ...v, [key]: value }))
    setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => k !== key)))
    setJustSaved((saved) => withoutKey(saved, key))
  }

  async function saveKey(key: string, value: string) {
    setSaving(key)
    setJustSaved((saved) => withoutKey(saved, key))
    setErrors((prev) => ({ ...prev, [key]: "" }))
    try {
      // PUT answers with the fresh map; use it (and refresh the cache) rather than refetching, because
      // fetchQuery would resolve with the pre-save map while the cached copy is still within staleTime.
      const updated = await api.config.update(key, value)
      // Only this key: the response is a whole map from the moment of its own save, so replacing the cache with a
      // slower, older response would revert a key another save has since changed.
      qc.setQueryData<Record<string, string>>(["config"], (cached) => mergeSavedConfigValue(cached ?? updated, updated, key))
      setValues((prev) => mergeSavedConfigValue(prev, updated, key))
      setJustSaved((saved) => ({ ...saved, [key]: true }))
    } catch (err) {
      setErrors((prev) => ({ ...prev, [key]: err instanceof Error ? err.message : "Save failed" }))
    } finally {
      setSaving(null)
    }
  }

  function renderField(field: ConfigField) {
    const isSavingField = saving === field.key
    const saveContent: React.ReactNode = isSavingField ? <CircleNotch className="size-3 animate-spin" aria-hidden="true" /> : "Save"
    const fieldId = `cfg-${field.key}`
    const shown = visibleValue(field)
    const errorId = `${fieldId}-error`
    const hintId = `${fieldId}-hint`
    const unchanged = isUnchanged(field, shown)
    // Edited since the page loaded and different from what the server holds: only then is there anything unsaved,
    // and only then is a rule worth shouting about. A field nobody touched may be empty (a key never stored).
    const edited = values[field.key] !== undefined && values[field.key] !== config?.[field.key]
    const rule = unchanged ? null : validateConfigValue(field, shown)
    const problem = edited ? rule : null
    const range = field.type === "number" ? limitsText(field.key) : null
    const serverError = errors[field.key]
    const descId = `${fieldId}-desc`
    const describedBy = [serverError ? errorId : null, problem ? hintId : null, descId].filter(Boolean).join(" ")

    return (
      <div key={field.key} className="p-4 max-w-lg">
        <label htmlFor={fieldId} className="text-xs font-medium text-foreground block mb-1">{field.label}</label>
        <div className="flex items-center gap-2">
          {field.type === "boolean" ? (
            <select
              id={fieldId}
              value={shown}
              onChange={(e) => edit(field.key, e.target.value)}
              aria-describedby={describedBy}
              className="h-8 border border-border bg-transparent px-2 font-mono text-xs"
            >
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </select>
          ) : field.type === "select" ? (
            <select
              id={fieldId}
              value={shown}
              onChange={(e) => edit(field.key, e.target.value)}
              aria-describedby={describedBy}
              className="h-8 border border-border bg-transparent px-2 font-mono text-xs"
            >
              {field.options?.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          ) : (
            <Input
              id={fieldId}
              value={shown}
              onChange={(e) => edit(field.key, e.target.value)}
              aria-describedby={describedBy}
              aria-invalid={problem ? true : undefined}
              type={field.type === "number" ? "number" : "text"}
              className="font-mono text-xs"
            />
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={() => saveKey(field.key, shown)}
            disabled={isSavingField || unchanged || rule !== null}
            aria-label={`Save ${field.label}`}
          >
            {saveContent}
          </Button>
          {justSaved[field.key] ? (
            <span role="status" className="text-xs text-primary">Saved</span>
          ) : (
            edited && !isSavingField && <span className="text-xs text-muted-foreground">Unsaved change</span>
          )}
        </div>
        {problem && <p id={hintId} className="text-xs text-destructive mt-1">{problem}</p>}
        {serverError && (
          <p id={errorId} role="alert" className="text-xs text-destructive mt-1">{serverError}</p>
        )}
        <p id={descId} className="text-xs text-muted-foreground mt-1">
          {field.description}
          {range && <> Allowed: {range}.</>}
        </p>
      </div>
    )
  }

  if (isLoading) {
    return (
      <div className="card px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
        <CircleNotch className="size-3.5 animate-spin" /> Loading config…
      </div>
    )
  }

  const header = (
    <div className="px-4 py-3 border-b border-border">
      <span className="section-label">Instance configuration</span>
      <p className="text-xs text-muted-foreground mt-0.5">Visible to instance owner only.</p>
    </div>
  )

  // Nothing has ever loaded: render no fields at all. Showing the form here would present invented
  // defaults as the real settings, and Save would write them over the server's values.
  if (isError && !config) {
    return (
      <div className="card">
        {header}
        <SectionError
          message={error instanceof Error ? error.message : "Failed to load config."}
          onRetry={() => refetch()}
          retrying={isFetching}
        />
      </div>
    )
  }

  return (
    <div className="card">
      {header}
      {isError && (
        <SectionError
          message={error instanceof Error ? error.message : "Failed to load config."}
          onRetry={() => refetch()}
          retrying={isFetching}
        />
      )}
      {CONFIG_GROUPS.map((group) => {
        const fields = group.keys.flatMap((key) => CONFIG_FIELDS.filter((f) => f.key === key))
        return (
          <section key={group.id} aria-labelledby={`cfg-group-${group.id}`}>
            <h3 id={`cfg-group-${group.id}`} className="section-label px-4 pt-4 pb-1">
              {group.label}
            </h3>
            <div className="divide-y divide-border">{fields.map(renderField)}</div>
          </section>
        )
      })}
    </div>
  )
}


// The page's sections, in order; the admin-only ones are only there (and only linked) for a workspace admin.
const SECTIONS = [
  { id: "profile", label: "Profile" },
  { id: "password-email", label: "Password and email" },
  { id: "sessions", label: "Sessions" },
  { id: "appearance", label: "Appearance" },
  { id: "organizations", label: "Organizations" },
  { id: "github-app", label: "GitHub App", adminOnly: true },
  { id: "connected", label: "Connected accounts" },
  { id: "tokens", label: "Saved tokens", adminOnly: true },
  { id: "instance", label: "Instance configuration", adminOnly: true },
  { id: "delete-account", label: "Delete account" },
] as const

export default function SettingsPage() {
  const { user } = useAuth()
  const admin = !!user?.is_workspace_admin
  const sections = SECTIONS.filter((s) => admin || !("adminOnly" in s))
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const [justInstalled, setJustInstalled] = useState(false)
  // What the GitHub link callback reported (`?github=linked` or `?github_link_error=<code>`), shown once.
  const [linkResult, setLinkResult] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    const linkError = searchParams.get("github_link_error")
    const linked = searchParams.get("github") === "linked"
    if (searchParams.get("installed") === "1") {
      setJustInstalled(true)
      router.replace("/settings")
    } else if (linkError) {
      setLinkResult({ ok: false, text: githubLinkErrorMessage(linkError) })
      queryClient.invalidateQueries({ queryKey: ["me"] })
      router.replace("/settings")
    } else if (linked) {
      setLinkResult({ ok: true, text: "GitHub account linked." })
      // The profile and the org list both depend on the link.
      queryClient.invalidateQueries({ queryKey: ["me"] })
      router.replace("/settings")
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <>
      <PageHeader title="Settings" description="Configure your workspace." />
      {justInstalled && (
        <div className="mb-4 px-4 py-2.5 bg-primary/10 border border-primary/30 text-sm text-primary flex items-center gap-1.5">
          <CheckCircle className="size-3.5" /> GitHub App installation connected.
        </div>
      )}
      {linkResult && (
        <div
          role={linkResult.ok ? "status" : "alert"}
          className={`mb-4 px-4 py-2.5 border text-sm flex items-center gap-1.5 ${
            linkResult.ok ? "bg-primary/10 border-primary/30 text-primary" : "bg-destructive/10 border-destructive/30 text-destructive"
          }`}
        >
          {linkResult.text}
        </div>
      )}
      <SectionNav sections={sections} />
      <div className="flex flex-col gap-6">
        <SectionAnchor id="profile">
          <ProfileSection />
        </SectionAnchor>
        <SectionAnchor id="password-email">
          {user?.has_password === false ? (
            <div className="card">
              <div className="px-4 py-3 border-b border-border">
                <span className="section-title">Password and email</span>
              </div>
              <p className="p-4 text-sm text-muted-foreground">
                You sign in with GitHub; your email and password are managed there.
              </p>
            </div>
          ) : (
            <>
              <ChangePasswordCard />
              <ChangeEmailCard />
            </>
          )}
        </SectionAnchor>
        <SectionAnchor id="sessions">
          <SessionsCard />
        </SectionAnchor>
        <SectionAnchor id="appearance">
          <AppearanceSection />
        </SectionAnchor>
        <SectionAnchor id="organizations">
          <OrgMembershipsSection />
          <PendingInstallationsCard />
        </SectionAnchor>
        {admin && (
          <SectionAnchor id="github-app">
            <GithubAppSetupCard />
          </SectionAnchor>
        )}
        <SectionAnchor id="connected">
          <ConnectedOrgsSection />
        </SectionAnchor>
        {admin && (
          <SectionAnchor id="tokens">
            <SavedTokensSection />
          </SectionAnchor>
        )}
        {admin && (
          <SectionAnchor id="instance">
            <InstanceConfigSection />
          </SectionAnchor>
        )}
        <SectionAnchor id="delete-account">
          <DeleteAccountCard />
        </SectionAnchor>
      </div>
    </>
  )
}
