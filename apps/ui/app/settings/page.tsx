"use client"

import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Trash, Plus, CircleNotch, Check, ArrowSquareOut } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"
import { THEMES, useTheme } from "@/lib/theme"
import type { InstallationMeta, SavedTokenMeta } from "@/lib/api/types"

// ── Profile section ──────────────────────────────────────────────────────────

function ProfileSection() {
  const { user, updateUser } = useAuth()
  const [name, setName] = useState(user?.name || "")
  const [org, setOrg] = useState("")
  const [saved, setSaved] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    setName(user?.name || "")
    setOrg(localStorage.getItem("default_org") || "")
  }, [user])

  async function save() {
    setIsSaving(true)
    try {
      if (name.trim() !== (user?.name || "")) {
        const updated = await api.auth.patchMe(name.trim())
        updateUser({ name: updated.name })
      }
      localStorage.setItem("default_org", org.trim())
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="bg-card border border-border">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Profile</span>
      </div>
      <div className="p-4 flex flex-col gap-3 max-w-sm">
        <div>
          <label className="text-xs font-medium text-foreground block mb-1.5">Display name</label>
          <Input
            placeholder="Your name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-foreground block mb-1.5">Email</label>
          <Input value={user?.email || ""} disabled className="opacity-60 cursor-not-allowed" />
          <p className="text-xs text-muted-foreground mt-1">Email cannot be changed.</p>
        </div>
        <div>
          <label className="text-xs font-medium text-foreground block mb-1.5">Default organization</label>
          <Input
            placeholder="e.g. octocat"
            value={org}
            onChange={(e) => setOrg(e.target.value)}
          />
          <p className="text-xs text-muted-foreground mt-1">
            Pre-fills the org field on Health &amp; Security and other pages.
          </p>
        </div>
        <Button onClick={save} disabled={isSaving} className="mt-1 w-fit">
          {saved ? <><Check className="size-3.5" />Saved</> : isSaving ? <><CircleNotch className="size-3.5 animate-spin" />Saving…</> : "Save profile"}
        </Button>
      </div>
    </div>
  )
}

// ── Appearance section ───────────────────────────────────────────────────────

function AppearanceSection() {
  const { theme, setTheme } = useTheme()

  return (
    <div className="bg-card border border-border">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Appearance</span>
        <p className="text-xs text-muted-foreground mt-0.5">Theme is saved to this browser.</p>
      </div>
      <div className="p-4 grid grid-cols-2 sm:grid-cols-3 gap-2">
        {THEMES.map((t) => {
          const active = theme === t.name
          return (
            <button
              key={t.name}
              onClick={() => setTheme(t.name)}
              aria-pressed={active}
              className={[
                "flex items-center gap-2.5 px-3 py-2.5 border text-left transition-colors",
                active ? "border-primary bg-primary/10" : "border-border hover:bg-elevated",
              ].join(" ")}
            >
              <span
                data-theme={t.name}
                className="flex shrink-0 overflow-hidden rounded-none border border-border/60"
              >
                <span className="size-3.5 bg-background" />
                <span className="size-3.5 bg-card" />
                <span className="size-3.5 bg-primary" />
              </span>
              <span className="flex-1 text-xs font-medium text-foreground">{t.label}</span>
              {active && <Check className="size-3.5 text-primary shrink-0" />}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Shared inline error state ────────────────────────────────────────────────

function SectionError({ message, onRetry, retrying }: { message: string; onRetry: () => void; retrying?: boolean }) {
  return (
    <div className="px-4 py-6 flex items-center justify-between gap-3">
      <p className="text-sm text-destructive">{message}</p>
      <Button size="sm" variant="outline" onClick={onRetry} disabled={retrying}>
        {retrying ? <CircleNotch className="size-3 animate-spin" /> : "Retry"}
      </Button>
    </div>
  )
}

// ── Connected organizations section (GitHub App) ─────────────────────────────

function ConnectedOrgsSection() {
  const { data: installs = [], isLoading, isError, error, isFetching, refetch } = useQuery<InstallationMeta[]>({
    queryKey: ["installations"],
    queryFn: () => api.installations.list(),
  })
  const slug = process.env.NEXT_PUBLIC_GITHUB_APP_SLUG
  const installUrl = slug ? `https://github.com/apps/${slug}/installations/new` : null

  return (
    <div className="bg-card border border-border">
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">Connected organizations</span>
        {installs.length > 0 && <span className="stat-chip">{installs.length} connected</span>}
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
      ) : installs.length === 0 ? (
        <div className="px-4 py-6">
          <p className="text-sm text-muted-foreground">
            No organizations connected yet. Install the Clevis GitHub App on a GitHub organization to get started.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Organization</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Type</th>
                <th className="text-left text-muted-foreground font-medium px-4 py-2">Connected</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {installs.map((i) => (
                <tr key={i.id} className="hover:bg-elevated transition-colors">
                  <td className="px-4 py-2.5 font-mono text-foreground/80">{i.account_login}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{i.account_type}</td>
                  <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">
                    {new Date(i.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="border-t border-border p-4">
        {installUrl ? (
          <Button onClick={() => { window.location.href = installUrl }}>
            <ArrowSquareOut className="size-3.5" />Install GitHub App
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">
            Set <code className="font-mono">NEXT_PUBLIC_GITHUB_APP_SLUG</code> to enable the install button.
          </p>
        )}
      </div>
    </div>
  )
}

// ── Saved tokens section (legacy — being replaced by the GitHub App) ──────────

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
      qc.invalidateQueries({ queryKey: ["tokens"] })
      setAddOrg("")
      setAddToken("")
      setAddLabel("")
    },
  })

  const remove = useMutation({
    mutationFn: (org: string) => api.tokens.delete(org),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tokens"] }),
  })

  const canAdd = addOrg.trim().length > 0 && addToken.trim().length > 0

  return (
    <div className="bg-card border border-border">
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center justify-between">
          <span className="section-label">Personal access tokens (legacy)</span>
          {tokens.length > 0 && (
            <span className="stat-chip">{tokens.length} saved</span>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">
          Being replaced by the GitHub App. Still used by Health &amp; Security and Cache pages until they move to the App.
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
                  <td className="px-4 py-2.5 font-mono text-foreground/80">{t.org}</td>
                  <td className="px-4 py-2.5 text-muted-foreground">{t.label ?? "—"}</td>
                  <td className="px-4 py-2.5 text-muted-foreground whitespace-nowrap">
                    {new Date(t.created_at).toLocaleDateString(undefined, {
                      month: "short", day: "numeric", year: "numeric",
                    })}
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => remove.mutate(t.org)}
                      disabled={remove.isPending}
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

      <div className="border-t border-border p-4">
        <p className="text-xs font-medium text-foreground mb-3">Add token</p>
        <div className="grid gap-2 sm:grid-cols-3">
          <Input
            placeholder="Org or owner"
            value={addOrg}
            onChange={(e) => setAddOrg(e.target.value)}
          />
          <Input
            placeholder="ghp_… token"
            type="password"
            value={addToken}
            onChange={(e) => setAddToken(e.target.value)}
            className="font-mono"
          />
          <Input
            placeholder="Label (optional)"
            value={addLabel}
            onChange={(e) => setAddLabel(e.target.value)}
          />
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

// ── Instance configuration section (owner only) ──────────────────────────────

const CONFIG_FIELDS: { key: string; label: string; description: string; type?: string }[] = [
  { key: "worker_poll_seconds", label: "Worker Poll Interval",    description: "Seconds between job queue polls.", type: "number" },
  { key: "registration_enabled", label: "Self-Registration",     description: "Allow anyone to create an account via /register.", type: "boolean" },
]

function InstanceConfigSection() {
  const { data: config, isLoading, isError, error, isFetching, refetch } = useQuery<Record<string, string>>({
    queryKey: ["config"],
    queryFn: api.config.getAll,
  })
  const qc = useQueryClient()
  const [saving, setSaving] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    if (config) setValues(config)
  }, [config])

  async function saveKey(key: string) {
    setSaving(key)
    setErrors((prev) => ({ ...prev, [key]: "" }))
    try {
      await api.config.update(key, values[key] ?? "")
      qc.invalidateQueries({ queryKey: ["config"] })
    } catch (err) {
      setErrors((prev) => ({ ...prev, [key]: err instanceof Error ? err.message : "Save failed" }))
    } finally {
      setSaving(null)
    }
  }

  if (isLoading) {
    return (
      <div className="bg-card border border-border px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
        <CircleNotch className="size-3.5 animate-spin" /> Loading config…
      </div>
    )
  }

  return (
    <div className="bg-card border border-border">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Instance configuration</span>
        <p className="text-xs text-muted-foreground mt-0.5">Visible to instance owner only.</p>
      </div>
      {isError && (
        <SectionError
          message={error instanceof Error ? error.message : "Failed to load config."}
          onRetry={() => refetch()}
          retrying={isFetching}
        />
      )}
      <div className="divide-y divide-border">
        {CONFIG_FIELDS.map((field) => (
          <div key={field.key} className="p-4 max-w-lg">
            <label className="text-xs font-medium text-foreground block mb-1">{field.label}</label>
            <div className="flex items-center gap-2">
              {field.type === "boolean" ? (
                <select
                  value={values[field.key] ?? "true"}
                  onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                  className="h-8 border border-border bg-transparent px-2 font-mono text-xs"
                >
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              ) : (
                <Input
                  value={values[field.key] ?? ""}
                  onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
                  type={field.type === "number" ? "number" : "text"}
                  className="font-mono text-xs"
                />
              )}
              <Button size="sm" variant="outline" onClick={() => saveKey(field.key)} disabled={saving === field.key}>
                {saving === field.key ? <CircleNotch className="size-3 animate-spin" /> : "Save"}
              </Button>
            </div>
            {errors[field.key] && (
              <p className="text-xs text-destructive mt-1">{errors[field.key]}</p>
            )}
            <p className="text-xs text-muted-foreground mt-1">{field.description}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { user } = useAuth()

  return (
    <>
      <PageHeader title="Settings" description="Configure your workspace." />
      <div className="flex flex-col gap-4">
        <ProfileSection />
        <AppearanceSection />
        <ConnectedOrgsSection />
        <SavedTokensSection />
        {user?.is_owner && <InstanceConfigSection />}
      </div>
    </>
  )
}
