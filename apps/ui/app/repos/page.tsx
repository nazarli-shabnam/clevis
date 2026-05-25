"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { PageHeader } from "@/components/page-header"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"

export default function ReposPage() {
  const router = useRouter()
  const [owner, setOwner] = useState("")
  const [repo, setRepo] = useState("")

  function navigate() {
    if (owner && repo) {
      router.push(`/repos/${owner}~${repo}/cache`)
    }
  }

  return (
    <>
      <PageHeader
        title="Cache Management"
        description="Manage GitHub Actions caches for your repositories."
      />

      <div className="bg-card border border-border max-w-sm">
        <div className="px-4 py-3 border-b border-border">
          <span className="section-title">Select repository</span>
        </div>
        <div className="p-4 flex flex-col gap-3">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1.5">Owner</label>
            <Input
              placeholder="e.g. octocat"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-foreground block mb-1.5">Repository</label>
            <Input
              placeholder="e.g. hello-world"
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && navigate()}
            />
          </div>
          <Button onClick={navigate} disabled={!owner || !repo} className="mt-1">
            Open cache manager
          </Button>
        </div>
      </div>
    </>
  )
}
