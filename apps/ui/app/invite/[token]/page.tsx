"use client"

import { useParams, useRouter } from "next/navigation"
import { useMutation, useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"

export default function InviteAcceptPage() {
  const params = useParams<{ token: string }>()
  const router = useRouter()
  const { user, isLoading: authLoading } = useAuth()
  const token = params.token

  const { data: preview, isLoading, isError, error } = useQuery({
    queryKey: ["invitation-preview", token],
    queryFn: () => api.invitations.preview(token),
  })

  const accept = useMutation({
    mutationFn: () => api.invitations.accept(token),
    onSuccess: () => router.push("/"),
  })

  return (
    <div className="max-w-md mx-auto mt-16">
      <PageHeader title="Org invitation" description="Accept an invitation to join an organization on Clevis." />

      <div className="bg-card border border-border">
        <div className="p-4 flex flex-col gap-3">
          {isLoading || authLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" /> Loading…
            </div>
          ) : isError || !preview ? (
            <p className="text-sm text-destructive flex items-center gap-1.5">
              <AlertTriangle className="size-3.5" />
              {error instanceof Error ? error.message : "Invitation not found"}
            </p>
          ) : preview.status !== "pending" ? (
            <p className="text-sm text-muted-foreground">This invitation is no longer valid.</p>
          ) : (
            <>
              <p className="text-sm text-foreground">
                You&rsquo;ve been invited to join <span className="font-semibold">{preview.org_login}</span> as a
                member.
              </p>
              {!user ? (
                <p className="text-xs text-muted-foreground">
                  Sign in with the account this invite was sent to, and you&rsquo;ll be brought back here to accept.
                  {" "}
                  <a
                    href={`/login?next=${encodeURIComponent(`/invite/${token}`)}`}
                    className="text-primary hover:underline"
                  >
                    Sign in
                  </a>
                  {" "}(GitHub sign-in returns you to the dashboard instead — just revisit this link afterward).
                </p>
              ) : accept.isSuccess ? (
                <p className="text-sm text-primary flex items-center gap-1.5">
                  <CheckCircle2 className="size-3.5" /> Joined {preview.org_login} — redirecting…
                </p>
              ) : (
                <Button onClick={() => accept.mutate()} disabled={accept.isPending}>
                  {accept.isPending ? "Joining…" : "Accept invitation"}
                </Button>
              )}
              {accept.isError && <p className="text-xs text-destructive">{accept.error.message}</p>}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
