"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { SectionError } from "@/components/section-error"
import type { HygieneScoringSettings } from "@/lib/api/types"

type Choice = "follow" | "on" | "off"

const toChoice = (enabled: boolean | null): Choice => (enabled === null ? "follow" : enabled ? "on" : "off")
const fromChoice = (choice: Choice): boolean | null => (choice === "follow" ? null : choice === "on")

/** Org admins choose whether the repo-hygiene checks (CODEOWNERS, SECURITY.md, ...) count toward this org's score. */
export function HygieneScoringCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["hygiene-scoring", orgLogin]
  const query = useQuery<HygieneScoringSettings>({
    queryKey,
    queryFn: () => api.orgs.hygieneScoring(orgLogin),
    retry: false,
  })
  const update = useMutation({
    mutationFn: (choice: Choice) => api.orgs.setHygieneScoring(orgLogin, fromChoice(choice)),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
  })

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Security score</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load the score settings: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <div className="p-4 flex flex-col gap-2">
          <label htmlFor="hygiene-scoring" className="text-xs font-medium text-foreground">
            Count repo hygiene checks toward the score
          </label>
          <select
            id="hygiene-scoring"
            value={toChoice(query.data.enabled)}
            disabled={update.isPending}
            onChange={(e) => update.mutate(e.target.value as Choice)}
            className="text-xs card text-foreground px-2 py-1.5 max-w-72 focus:outline-none focus:ring-1 focus:ring-ring"
          >
            <option value="follow">Follow the instance setting ({query.data.instance_default ? "on" : "off"})</option>
            <option value="on">Always count them</option>
            <option value="off">Never count them</option>
          </select>
          <p className="text-xs text-muted-foreground">
            CODEOWNERS, SECURITY.md, license, stale branches and unpinned Actions. Currently{" "}
            {query.data.effective ? "counted in" : "left out of"} this organization&rsquo;s score; the change applies to the next scan.
          </p>
          {update.isError && <p className="text-xs text-destructive">{update.error.message}</p>}
        </div>
      )}
    </div>
  )
}
