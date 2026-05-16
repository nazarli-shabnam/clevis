"use client"

// URL: /repos/<owner~name>/cache. The [repo] folder name is Next.js dynamic segment
// syntax (not a literal path); param holds owner and repo joined with "~" (see repos/page.tsx).

import { useParams } from "next/navigation"
import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Database, Trash2, Eye, Loader2 } from "lucide-react"
import { api } from "@/lib/api/client"
import type { CacheEntry } from "@/lib/api/types"

export default function CachePage() {
  const params = useParams<{ repo: string }>()
  const [token, setToken] = useState("")
  const [actor, setActor] = useState("admin@example.local")

  const [owner, repo] = (params.repo || "").split("~")

  function formatBytes(bytes: number) {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const listMutation = useMutation({
    mutationFn: () => api.cache.list(owner, repo, token),
  })

  const clearMutation = useMutation({
    mutationFn: (dryRun: boolean) =>
      api.cache.clear(owner, repo, { token, actor, dry_run: dryRun }),
  })

  const isLoading = listMutation.isPending || clearMutation.isPending
  const caches: CacheEntry[] = listMutation.data?.actions_caches ?? []

  return (
    <>
      <PageHeader
        title="Actions Cache"
        description={`Repository: ${owner}/${repo}`}
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="size-5" />
              Configuration
            </CardTitle>
            <CardDescription>Authentication and actor settings</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <Input
              placeholder="GitHub token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
            <Input
              placeholder="Actor"
              value={actor}
              onChange={(e) => setActor(e.target.value)}
            />
            <Button onClick={() => listMutation.mutate()} disabled={isLoading || !token}>
              {listMutation.isPending ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  Loading...
                </>
              ) : (
                "Load caches"
              )}
            </Button>
            <div className="flex gap-2">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => clearMutation.mutate(true)}
                disabled={isLoading || !token}
              >
                <Eye className="mr-2 size-4" />
                Dry run
              </Button>
              <Button
                variant="destructive"
                className="flex-1"
                onClick={() => clearMutation.mutate(false)}
                disabled={isLoading || !token}
              >
                <Trash2 className="mr-2 size-4" />
                Clear
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Caches ({caches.length})</CardTitle>
            <CardDescription>Actions cache entries for this repository</CardDescription>
          </CardHeader>
          <CardContent>
            {caches.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No caches loaded. Click &quot;Load caches&quot; to fetch.
              </p>
            ) : (
              <div className="divide-y">
                {caches.map((c) => (
                  <div
                    key={c.id}
                    className="flex items-center justify-between py-3 first:pt-0 last:pb-0"
                  >
                    <span className="truncate font-mono text-sm">{c.key}</span>
                    <Badge variant="secondary">{formatBytes(c.size_in_bytes)}</Badge>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {clearMutation.data && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Result</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-auto rounded-md bg-muted p-4 text-sm">
              {JSON.stringify(clearMutation.data, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </>
  )
}
