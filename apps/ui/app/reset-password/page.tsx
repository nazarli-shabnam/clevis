"use client"

import { Suspense, useState } from "react"
import { useMutation } from "@tanstack/react-query"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { CheckCircle, CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"

function ResetPasswordForm() {
  const token = useSearchParams().get("token")
  const [password, setPassword] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const reset = useMutation({
    mutationFn: () => api.auth.resetPassword(token ?? "", password),
    onError: (e) => setError(e instanceof Error ? e.message : "Couldn't reset the password"),
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    if (password.length < 12) return setError("Password must be at least 12 characters.")
    if (password !== confirm) return setError("Passwords do not match.")
    reset.mutate()
  }

  return (
    <main className="min-h-screen bg-background flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8">
          <p className="text-[0.6875rem] font-medium text-muted-foreground uppercase tracking-[0.12em] mb-1">clevis</p>
          <h1 className="text-2xl font-semibold text-foreground">Choose a new password</h1>
        </div>

        {!token ? (
          <div role="alert" className="text-sm text-destructive flex flex-col gap-3">
            <p>This reset link is missing its token.</p>
            <Link href="/forgot-password" className="text-primary hover:underline">Ask for a new link</Link>
          </div>
        ) : reset.isSuccess ? (
          <div role="status" className="text-sm text-primary flex flex-col gap-3">
            <p className="flex items-center gap-1.5">
              <CheckCircle className="size-3.5" /> Your password is changed. You were signed out everywhere.
            </p>
            <Link href="/login" className="hover:underline">Sign in</Link>
          </div>
        ) : (
          <form onSubmit={submit} className="flex flex-col gap-3">
            <Field>
              <FieldLabel>New password</FieldLabel>
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
              <FieldLabel>Confirm new password</FieldLabel>
              <Input
                type="password"
                placeholder="Repeat password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
              />
            </Field>
            {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
            <Button type="submit" disabled={reset.isPending} className="mt-2">
              {reset.isPending ? <><CircleNotch className="size-3.5 animate-spin" />Saving…</> : "Set new password"}
            </Button>
            {reset.isError && (
              <Link href="/forgot-password" className="text-xs text-primary text-center hover:underline">
                Ask for a new link
              </Link>
            )}
          </form>
        )}
      </div>
    </main>
  )
}

export default function ResetPasswordPage() {
  return (
    <Suspense>
      <ResetPasswordForm />
    </Suspense>
  )
}
