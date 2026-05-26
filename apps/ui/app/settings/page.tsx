"use client"

import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Trash2, Plus, Loader2, Check } from "lucide-react"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import type { SavedTokenMeta } from "@/lib/api/types"

// ── Profile section ──────────────────────────────────────────────────────────

function ProfileSection() {
  const [name, setName] = useState("")
  const [org, setOrg] = useState("")
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setName(localStorage.getItem("profile_name") || "")
    setOrg(localStorage.getItem("default_org") || "")
  }, [])

  function save() {
    localStorage.setItem("profile_name", name.trim() || "Guest")
    localStorage.setItem("default_org", org.trim())
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    // Refresh sidebar without full page reload
    window.dispatchEvent(new Event("profile-updated"))
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
        <Button onClick={save} className="mt-1 w-fit">
          {saved ? <><Check className="size-3.5" />Saved</> : "Save profile"}
        </Button>
      </div>
    </div>
  )
}

// ── Saved tokens section ─────────────────────────────────────────────────────

function SavedTokensSection() {
  const qc = useQueryClient()
  const [addOrg, setAddOrg] = useState("")
  const [addToken, setAddToken] = useState("")
  const [addLabel, setAddLabel] = useState("")

  const { data: tokens = [], isLoading } = useQuery<SavedTokenMeta[]>({
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
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <span className="section-label">Saved tokens</span>
        {tokens.length > 0 && (
          <span className="stat-chip">{tokens.length} saved</span>
        )}
      </div>

      {/* Token list */}
      {isLoading ? (
        <div className="px-4 py-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Loading…
        </div>
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
                      <Trash2 className="size-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add new token */}
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
            <><Loader2 className="size-3.5 animate-spin" />Saving…</>
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

// ── Page ─────────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Configure your workspace." />
      <div className="flex flex-col gap-4">
        <ProfileSection />
        <SavedTokensSection />
      </div>
    </>
  )
}
