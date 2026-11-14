"use client"

import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useOptionalAuth } from "@/lib/auth-context"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { DigestSettings } from "@/lib/api/types"

/** Shown to org admins when the server has no SMTP: an invitee who registers with a password can never click
 * a verification link, so can't accept. A workspace admin can verify such an account by hand. */
export function SmtpVerifyNotice({ orgLogin }: { orgLogin: string }) {
  const canVerify = !!useOptionalAuth()?.user?.is_workspace_admin
  // The same query as the digest card (it also reports whether SMTP is set up).
  const settings = useQuery<DigestSettings>({
    queryKey: ["digest", orgLogin],
    queryFn: () => api.orgs.digest(orgLogin),
    retry: false,
  })
  const [email, setEmail] = useState("")
  const verify = useMutation({ mutationFn: (address: string) => api.auth.adminVerifyEmail(address) })

  if (!settings.data || settings.data.smtp_configured) return null
  const address = email.trim()
  return (
    <div className="card mb-4 px-4 py-3 text-xs" role="note">
      <p className="font-medium text-foreground">Email isn&apos;t set up on this server</p>
      <p className="text-muted-foreground mt-1">
        People you invite who register with a password can&apos;t verify their email, so they can&apos;t accept an
        invitation until it&apos;s verified for them.{" "}
        {canVerify
          ? "Enter their address to verify it yourself, but only after confirming with the person that the account is theirs: an invitation link can be forwarded, so registering with it doesn't prove they own the address."
          : "Ask a workspace admin to verify their address, or configure SMTP."}
      </p>
      {canVerify && (
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (address) verify.mutate(address)
          }}
        >
          <Input
            type="email"
            aria-label="Email address to verify"
            placeholder="invitee@example.com"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
              verify.reset()
            }}
            className="max-w-xs"
          />
          <Button type="submit" size="sm" variant="outline" disabled={!address || verify.isPending}>
            {verify.isPending ? "Verifying…" : "Mark verified"}
          </Button>
        </form>
      )}
      {verify.isSuccess && (
        <p role="status" className="mt-2 text-foreground">
          {verify.data.already_verified ? `${address} was already verified.` : `${address} is verified and can accept invitations.`}
        </p>
      )}
      {verify.isError && (
        <p role="alert" className="mt-2 text-destructive">
          Couldn&apos;t verify: {verify.error.message}
        </p>
      )}
    </div>
  )
}
