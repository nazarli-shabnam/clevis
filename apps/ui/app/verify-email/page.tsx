"use client"

import { useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { useMutation } from "@tanstack/react-query"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api/client"
import { CheckCircle, Warning, CircleNotch } from "@phosphor-icons/react"

export default function VerifyEmailPage() {
  const token = useSearchParams().get("token")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const verify = useMutation({
    mutationFn: () => api.auth.verifyEmail(token ?? "", password),
    onError: (e) => setError(e instanceof Error ? e.message : "Verification failed"),
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    if (password.length < 12) return setError("Password must be at least 12 characters.")
    if (password !== confirm) return setError("Passwords do not match.")
    verify.mutate()
  }

  return (
    // Public routes render outside the app shell (no sidebar, no shell <main>), so the page owns its landmark.
    <main className="max-w-md mx-auto mt-16 px-4">
      <PageHeader title="Verify your email" description="Confirming your Clevis account email address." />

      <div className="card">
        <div className="p-4 flex flex-col gap-3">
          {!token ? (
            <>
              <p role="alert" className="text-sm text-destructive flex items-center gap-1.5">
                <Warning className="size-3.5" /> This verification link is missing its token.
              </p>
              <Link href="/" className="text-sm text-primary hover:underline">
                Back to Clevis
              </Link>
            </>
          ) : verify.isSuccess ? (
            <>
              <p role="status" className="text-sm text-primary flex items-center gap-1.5">
                <CheckCircle className="size-3.5" /> Your email is verified and your password is set. You were signed
                out everywhere; sign in again to accept organization invitations.
              </p>
              <Link href="/login" className="text-sm text-primary hover:underline">
                Sign in
              </Link>
            </>
          ) : (
            <form onSubmit={submit} className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">
                Choose your password to finish verifying. Any password set before this step stops working.
              </p>
              <Field>
                <FieldLabel>Password</FieldLabel>
                <Input
                  type="password"
                  placeholder="At least 12 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                />
              </Field>
              <Field>
                <FieldLabel>Confirm password</FieldLabel>
                <Input
                  type="password"
                  placeholder="Repeat password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                  autoComplete="new-password"
                />
              </Field>
              {error && (
                <p role="alert" className="text-xs text-destructive">
                  {error}
                </p>
              )}
              <Button type="submit" disabled={verify.isPending}>
                {verify.isPending ? (
                  <>
                    <CircleNotch className="size-3.5 animate-spin" />
                    Verifying…
                  </>
                ) : (
                  "Verify email"
                )}
              </Button>
            </form>
          )}
        </div>
      </div>
    </main>
  )
}
