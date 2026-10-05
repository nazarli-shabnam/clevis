"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { SectionError } from "@/components/section-error"
import { badgeHtml, badgeMarkdown, badgeUrl } from "@/lib/badge"

const RATE_LIMIT_ISSUE = "https://github.com/nazarli-shabam/clevis/issues/577"

function Snippet({ label, value }: { label: string; value: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle")
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
      setState("copied")
    } catch {
      setState("failed")
    }
    setTimeout(() => setState("idle"), 2000)
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">{label}</span>
        <Button size="sm" variant="outline" onClick={copy} aria-label={`Copy ${label} snippet`}>
          {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
        </Button>
      </div>
      <code className="block text-xs font-mono bg-muted/30 border border-border/50 rounded-md p-2 break-all">{value}</code>
    </div>
  )
}

/** Org admins opt the public score badge in or out and copy its embed snippets. */
export function BadgeSettingsCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["badge", orgLogin]
  const query = useQuery({ queryKey, queryFn: () => api.orgs.badge(orgLogin), retry: false })
  const update = useMutation({
    mutationFn: (enabled: boolean) => api.orgs.setBadge(orgLogin, enabled),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })

  return (
    <div className="card mt-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Score badge</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load the badge setting: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-3">
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={query.data.enabled}
              disabled={update.isPending}
              onChange={(e) => update.mutate(e.target.checked)}
            />
            Show a public security-score badge for {orgLogin}
          </label>
          <p className="text-xs text-muted-foreground">
            Only the bare score (a number) becomes public. The badge URL needs no sign-in, so anyone who has the link can
            see it, and it is cached for a few minutes. It is off until you turn it on; turning it off makes the URL return
            404. Request limits on this public route are tracked in{" "}
            <a href={RATE_LIMIT_ISSUE} target="_blank" rel="noreferrer" className="underline underline-offset-2">
              #577
            </a>
            .
          </p>
          {update.isError && <p className="text-xs text-destructive">{update.error.message}</p>}
          {query.data.enabled && (
            <>
              <div>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={badgeUrl(orgLogin)} alt="Clevis security score preview" className="h-5" />
                <p className="text-xs text-muted-foreground mt-1">
                  Live preview. If it shows a broken image, the org has not been scanned yet.
                </p>
              </div>
              <Snippet label="Markdown" value={badgeMarkdown(orgLogin)} />
              <Snippet label="HTML" value={badgeHtml(orgLogin)} />
            </>
          )}
        </div>
      )}
    </div>
  )
}
