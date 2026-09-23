"use client"

// URL: /repos/<owner~name>/cache; param holds owner and repo joined with "~".

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
        <div className="card px-4 py-6 text-sm text-muted-foreground">
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
