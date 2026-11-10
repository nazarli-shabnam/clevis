"use client"

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { EnvelopeSimple, X } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"

/** Shown to a signed-in user whose email is not verified: until it is, they cannot accept org
 * invitations. Reads the server's `email_verified` (the sign-in responses don't carry it), and goes away
 * once it is true; the X hides it until the page is next loaded. */
export function EmailVerificationBanner() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const [dismissed, setDismissed] = useState(false)

  const { data: me } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: api.auth.me,
    enabled: !!user,
    staleTime: 60_000,
  })

  const resend = useMutation({
    mutationFn: api.auth.resendVerification,
    onSuccess: (res) => {
      // Verified in the meantime (another tab, or the link): re-read so the banner goes away.
      if (res.already_verified) queryClient.invalidateQueries({ queryKey: ["auth", "me"] })
    },
  })

  // Only a definite `false`: while the query loads or if it fails there is nothing to claim.
  if (!user || dismissed || me?.email_verified !== false) return null

  const sent = resend.isSuccess && !resend.data.already_verified

  return (
    <div role="status" className="mb-4 flex items-start gap-3 border border-warning/30 bg-warning/5 px-3 py-2.5 text-xs">
      <EnvelopeSimple className="mt-0.5 size-4 shrink-0 text-warning" />
      <div className="flex-1 flex flex-col gap-1.5">
        <p className="text-foreground">
          Verify your email to accept invitations. We sent a link to <span className="font-mono">{me.email}</span>.
        </p>
        {sent && <p className="text-primary">Verification email sent. Check your inbox.</p>}
        {resend.isError && (
          <p role="alert" className="text-destructive">
            {resend.error.message}
          </p>
        )}
        {!sent && (
          <div>
            <Button size="sm" variant="outline" onClick={() => resend.mutate()} disabled={resend.isPending}>
              {resend.isPending ? "Sending…" : "Resend email"}
            </Button>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss"
        className="text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        <X className="size-3.5" />
      </button>
    </div>
  )
}
