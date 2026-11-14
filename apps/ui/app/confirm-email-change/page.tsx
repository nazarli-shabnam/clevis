"use client"

import { Suspense, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { CheckCircle, CircleNotch, Warning } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { api } from "@/lib/api/client"

function ConfirmEmailChange() {
  const token = useSearchParams().get("token")
  const [state, setState] = useState<"pending" | "success" | "error">("pending")
  const [message, setMessage] = useState("")
  // The link works once and Strict Mode runs effects twice: remember the token already sent.
  const attempted = useRef<string | null>(null)

  useEffect(() => {
    if (!token) {
      setState("error")
      setMessage("This confirmation link is missing its token.")
      return
    }
    if (attempted.current === token) return
    attempted.current = token
    api.auth
      .confirmEmailChange(token)
      .then(() => setState("success"))
      .catch((err) => {
        setState("error")
        setMessage(err instanceof Error ? err.message : "Confirmation failed")
      })
  }, [token])

  return (
    <main className="max-w-md mx-auto mt-16 px-4">
      <PageHeader title="Confirm your new email" description="Changing the email address on your Clevis account." />
      <div className="card">
        <div className="p-4 flex flex-col gap-3">
          {state === "pending" ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Confirming…
            </div>
          ) : state === "success" ? (
            <>
              <p className="text-sm text-primary flex items-center gap-1.5">
                <CheckCircle className="size-3.5" /> Your email is changed. Sign in again with the new address.
              </p>
              <Link href="/login" className="text-sm text-primary hover:underline">Sign in</Link>
            </>
          ) : (
            <>
              <p className="text-sm text-destructive flex items-center gap-1.5">
                <Warning className="size-3.5" /> {message}
              </p>
              <Link href="/settings" className="text-sm text-primary hover:underline">Back to settings</Link>
            </>
          )}
        </div>
      </div>
    </main>
  )
}

export default function ConfirmEmailChangePage() {
  return (
    <Suspense>
      <ConfirmEmailChange />
    </Suspense>
  )
}
