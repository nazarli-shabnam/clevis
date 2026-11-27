"use client"

import { useEffect, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { SectionError } from "@/components/section-error"
import { Button } from "@/components/ui/button"
import type { ScoringExclusions } from "@/lib/api/types"

const toLines = (patterns: string[]) => patterns.join("\n")
const toPatterns = (text: string) => text.split(/[\s,]+/).filter(Boolean)

/** Org admins list the repositories (names or `*` patterns) the security score leaves out, e.g. sandboxes and
 * test repos. Archived repositories are always left out; waivers stay the way to hide a failure for a while. */
export function ScoringExclusionsCard({ orgLogin }: { orgLogin: string }) {
  const queryClient = useQueryClient()
  const queryKey = ["scoring-exclusions", orgLogin]
  const query = useQuery<ScoringExclusions>({
    queryKey,
    queryFn: () => api.orgs.scoringExclusions(orgLogin),
    retry: false,
  })
  const [text, setText] = useState("")
  useEffect(() => {
    if (query.data) setText(toLines(query.data.patterns))
  }, [query.data])
  const save = useMutation({
    mutationFn: (patterns: string[]) => api.orgs.setScoringExclusions(orgLogin, patterns),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data)
      setText(toLines(data.patterns))
    },
  })
  const dirty = query.data !== undefined && toPatterns(text).join("\n") !== query.data.patterns.join("\n")

  return (
    <div className="card mb-4">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-title">Repositories left out of scoring</span>
      </div>
      {query.isLoading ? (
        <div className="px-4 py-4 flex items-center gap-2 text-sm text-muted-foreground">
          <CircleNotch className="size-3.5 animate-spin" /> Loading…
        </div>
      ) : query.isError || !query.data ? (
        <SectionError
          message={`Couldn't load the exclusions: ${query.error instanceof Error ? query.error.message : "unknown error"}`}
          onRetry={() => query.refetch()}
          retrying={query.isFetching}
        />
      ) : (
        <form
          className="p-4 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate(toPatterns(text))
          }}
        >
          <label htmlFor="scoring-exclusions" className="text-xs font-medium text-foreground">
            Repository names or patterns, one per line
          </label>
          <textarea
            id="scoring-exclusions"
            rows={4}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"sandbox-*\nlegacy-api"}
            spellCheck={false}
            className="text-xs font-mono card text-foreground px-2 py-1.5 max-w-96 focus:outline-none focus:ring-1 focus:ring-ring"
          />
          <p className="text-xs text-muted-foreground">
            <code>*</code> matches any run of characters and <code>?</code> one, ignoring case. Matching repositories
            are not checked and do not count toward the score from the next scan on; the Security page lists them.
            Archived repositories are always left out. To hide a failure only for a while, use a waiver instead.
          </p>
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" disabled={!dirty || save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Button>
            {save.isSuccess && !dirty && <span className="text-xs text-muted-foreground">Saved.</span>}
          </div>
          {save.isError && <p className="text-xs text-destructive">{save.error.message}</p>}
        </form>
      )}
    </div>
  )
}
