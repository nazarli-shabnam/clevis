"use client"

// URL: /repos/<owner~name>/cache. The [repo] folder name is Next.js dynamic segment
// syntax (not a literal path); param holds owner and repo joined with "~" (see repos/page.tsx).

import { useParams } from "next/navigation"
import { PageHeader } from "@/components/page-header"
import { CachePanel } from "@/components/repo/cache-panel"
import { parseOwnerRepo } from "@/lib/repo-segment"

export default function CachePage() {
  const params = useParams<{ repo: string }>()
  const parsed = parseOwnerRepo(params.repo || "")

  if (!parsed) {
    return (
      <>
        <PageHeader title="Actions Cache" description="Invalid repository route." />
        <div className="bg-card border border-border px-4 py-6 text-sm text-muted-foreground">
          Expected URL format: <span className="font-mono">/repos/owner~repo/cache</span>
        </div>
      </>
    )
  }

  return (
    <>
      <PageHeader title="Actions Cache" description={`${parsed.owner}/${parsed.repo}`} />
      <CachePanel owner={parsed.owner} repo={parsed.repo} />
    </>
  )
}
