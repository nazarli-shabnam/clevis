"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { useMutation, useQuery } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Warning, CheckCircle, CircleNotch } from "@phosphor-icons/react"
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

  // The backend 403s with this specific message when the account's email isn't verified
  // yet (issue #217) -- distinct from a genuine email mismatch, which also 403s but with
  // different wording, so only this case gets a resend affordance.
  const needsVerification = accept.isError && accept.error.message.toLowerCase().includes("verify")
  const [resendState, setResendState] = useState<"idle" | "sending" | "sent" | "error">("idle")

  async function resendVerification() {
    setResendState("sending")
    try {
      await api.auth.resendVerification()
      setResendState("sent")
    } catch {
      setResendState("error")
    }
  }

  return (
    <div className="max-w-md mx-auto mt-16">
      <PageHeader title="Org invitation" description="Accept an invitation to join an organization on Clevis." />

      <div className="card">
        <div className="p-4 flex flex-col gap-3">
          {isLoading || authLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Loading…
            </div>
          ) : isError || !preview ? (
            <p className="text-sm text-destructive flex items-center gap-1.5">
              <Warning className="size-3.5" />
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
                  , with either your password or GitHub.
                </p>
              ) : accept.isSuccess ? (
                <p className="text-sm text-primary flex items-center gap-1.5">
                  <CheckCircle className="size-3.5" /> Joined {preview.org_login} — redirecting…
                </p>
              ) : (
                <Button onClick={() => accept.mutate()} disabled={accept.isPending}>
                  {accept.isPending ? "Joining…" : "Accept invitation"}
                </Button>
              )}
              {accept.isError && (
                <div className="flex flex-col gap-1.5">
                  <p className="text-xs text-destructive">{accept.error.message}</p>
                  {needsVerification && (
                    resendState === "sent" ? (
                      <p className="text-xs text-primary">Verification email sent — check your inbox, then try again.</p>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={resendVerification}
                        disabled={resendState === "sending"}
                      >
                        {resendState === "sending" ? "Sending…" : "Resend verification email"}
                      </Button>
                    )
                  )}
                  {resendState === "error" && (
                    <p className="text-xs text-destructive">Couldn&rsquo;t resend the verification email. Try again shortly.</p>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
