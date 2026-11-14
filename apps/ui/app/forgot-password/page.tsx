"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import Link from "next/link"
import { CircleNotch } from "@phosphor-icons/react"
import { api } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Field, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("")
  const request = useMutation({ mutationFn: () => api.auth.forgotPassword(email.trim()) })

  return (
    <main className="min-h-screen bg-background flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8">
          <p className="text-[0.6875rem] font-medium text-muted-foreground uppercase tracking-[0.12em] mb-1">clevis</p>
          <h1 className="text-2xl font-semibold text-foreground">Forgot your password?</h1>
        </div>

        {request.isSuccess ? (
          <div role="status" className="text-sm text-muted-foreground flex flex-col gap-3">
            <p>
              If an account with a password exists for that address, we&apos;ve emailed a link to choose a new one. It
              works once and expires in an hour.
            </p>
            <p>
              Nothing arrived? Email may not be set up on this server; ask your administrator, who can set a password
              from the command line.
            </p>
            <Link href="/login" className="text-primary hover:underline">
              Back to sign in
            </Link>
          </div>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              request.mutate()
            }}
          >
            <Field>
              <FieldLabel>Email</FieldLabel>
              <Input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
              />
            </Field>
            {request.isError && (
              <p role="alert" className="text-xs text-destructive">{request.error.message}</p>
            )}
            <Button type="submit" disabled={request.isPending} className="mt-2">
              {request.isPending ? <><CircleNotch className="size-3.5 animate-spin" />Sending…</> : "Email me a reset link"}
            </Button>
            <Link href="/login" className="text-xs text-muted-foreground text-center hover:text-foreground">
              Back to sign in
            </Link>
          </form>
        )}
      </div>
    </main>
  )
}
