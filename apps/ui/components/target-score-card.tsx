"use client"

import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { TargetScore } from "@/lib/api/types"

/** Org admins set the score the org wants to stay at or above. Leave it empty for no target. While the latest
 * score is under it, the Security page shows a banner and a line on the trend chart, and members get a
 * "below target" notification (which each member can mute). */
export function TargetScoreCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["target-score", orgLogin]
  const query = useQuery<TargetScore>({
    queryKey,
    queryFn: () => api.orgs.targetScore(orgLogin),
    retry: false,
  })
  const [text, setText] = useState("")
  useEffect(() => {
    if (query.data) setText(query.data.target === null ? "" : String(query.data.target))
  }, [query.data])
  const save = useMutation({
    mutationFn: (target: number | null) => api.orgs.setTargetScore(orgLogin, target),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })
  const trimmed = text.trim()
  const parsed = trimmed === "" ? null : Number(trimmed)
  const valid = parsed === null || (Number.isInteger(parsed) && parsed >= 1 && parsed <= 100)
  const dirty = query.data !== undefined && parsed !== query.data.target

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Target score</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load the target score: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <form
          className="p-4 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (valid) save.mutate(parsed)
          }}
        >
          <label htmlFor="target-score" className="text-xs font-medium text-foreground">
            Keep the security score at or above (1-100)
          </label>
          <div className="flex items-center gap-2">
            <Input
              id="target-score"
              inputMode="numeric"
              className="w-24"
              placeholder="none"
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-invalid={!valid}
            />
            <Button type="submit" size="sm" disabled={!dirty || !valid || save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            {save.isSuccess && !dirty && <span className="text-xs text-muted-foreground">Saved.</span>}
          </div>
          {!valid && <p className="text-xs text-destructive">Enter a whole number from 1 to 100, or leave it empty for no target.</p>}
          <p className="text-xs text-muted-foreground">
            While the latest score is below it, the Security page shows a banner and a target line on the trend chart,
            and members get a &ldquo;below target&rdquo; notification (each member can mute that kind). Empty means no target.
          </p>
          {save.isError && <p className="text-xs text-destructive">{save.error.message}</p>}
        </form>
      )}
    </div>
  )
}
