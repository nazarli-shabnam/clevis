"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"

/** Says which repositories the org's score leaves out, so an excluded repo is marked rather than just absent.
 * Renders nothing when there are none, or when the setting cannot be read (a non-member scanning a login). */
export function ScoringExclusionsNote({ orgLogin, matched }: { orgLogin: string; matched?: string[] }) {
  const query = useQuery({
    queryKey: ["scoring-exclusions", orgLogin],
    queryFn: () => api.orgs.scoringExclusions(orgLogin),
    enabled: orgLogin.length > 0,
    retry: false,
  })
  const patterns = query.data?.patterns ?? []
  if (patterns.length === 0) return null
  return (
    <p className="mb-4 text-xs text-muted-foreground" role="note">
      The organization currently leaves these out of scoring: <span className="font-mono">{patterns.join(", ")}</span>
      {matched && matched.length > 0 && (
        <>
          {" "}
          (this scan skipped {matched.length}:{" "}
          <span className="font-mono">
            {matched.slice(0, 10).join(", ")}
            {matched.length > 10 ? ", …" : ""}
          </span>
          )
        </>
      )}
      . Archived repositories are always left out.{" "}
      <Link href={`/settings/org/${encodeURIComponent(orgLogin)}/settings#scanning`} className="text-primary hover:underline">
        Change in settings
      </Link>
    </p>
  )
}
