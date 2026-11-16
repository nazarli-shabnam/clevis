"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { CircleNotch, CheckCircle, Warning } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"

// GitHub redirects here (the manifest's redirect_url) after the admin clicks Create. The one-time `code` is
// exchanged by the API for the App's credentials, which it stores; nothing secret reaches the browser.
type Status = "working" | "success" | "error"

export default function GithubAppCallbackPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [status, setStatus] = useState<Status>("working")
  const [message, setMessage] = useState("")
  const [slug, setSlug] = useState<string | null>(null)
  const ranRef = useRef(false)
  const mountedRef = useRef(false)

  // Mounted flag rather than a cancel flag: under StrictMode the effect runs, cleans up and runs again, and a
  // cancel flag set by that simulated cleanup would swallow the single real result.
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (ranRef.current) return
    ranRef.current = true // the code is single-use: exchange it exactly once

    const code = searchParams.get("code")
    const state = searchParams.get("state")
    if (!code || !state) {
      setStatus("error")
      setMessage("GitHub didn't send a setup code. Start again from Settings.")
      return
    }
    api.githubApp
      .convert({ code, state })
      .then((res) => {
        if (!mountedRef.current) return
        setSlug(res.slug)
        setStatus("success")
      })
      .catch((err) => {
        if (!mountedRef.current) return
        setStatus("error")
        setMessage(err instanceof Error ? err.message : "Couldn't finish creating the GitHub App.")
      })
  }, [searchParams])

  return (
    <div className="max-w-md mx-auto mt-16">
      <PageHeader title="Creating your GitHub App" description="Finishing the setup GitHub just completed." />
      <div className="card">
        <div className="p-4 flex flex-col gap-3">
          {status === "working" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Storing the App&apos;s credentials…
            </div>
          )}
          {status === "success" && (
            <>
              <p className="text-sm text-primary flex items-center gap-1.5">
                <CheckCircle className="size-3.5" /> Created <strong>{slug}</strong>. Clevis is using it now, with no restart.
              </p>
              <p className="text-xs text-muted-foreground">Next, install it on your organization or personal account from Settings.</p>
              <Button size="sm" onClick={() => router.replace("/settings")}>
                Back to Settings
              </Button>
            </>
          )}
          {status === "error" && (
            <>
              <p className="text-sm text-destructive flex items-center gap-1.5">
                <Warning className="size-3.5" /> {message}
              </p>
              <Button size="sm" variant="outline" onClick={() => router.replace("/settings")}>
                Back to Settings
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
