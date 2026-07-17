"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { CircleNotch, CheckCircle, Warning } from "@phosphor-icons/react"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api/client"

// GitHub redirects here after "Install GitHub App" (this path must be configured as the
// GitHub App's "Setup URL" in the App's own settings on github.com — see AGENTS.md/README
// for the one-time manual step). It carries installation_id/setup_action as query params;
// this page resolves the account behind installation_id, then calls the matching sync
// endpoint (/me or /orgs/{org}) so a github_installations row actually gets persisted.
type Status = "working" | "pending-approval" | "success" | "error"

export default function GithubInstallCallbackPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [status, setStatus] = useState<Status>("working")
  const [errorMessage, setErrorMessage] = useState("")
  const ranRef = useRef(false)

  useEffect(() => {
    if (ranRef.current) return
    ranRef.current = true

    const installationId = searchParams.get("installation_id")
    const setupAction = searchParams.get("setup_action")

    if (setupAction === "request") {
      setStatus("pending-approval")
      return
    }

    if (!installationId || Number.isNaN(Number(installationId))) {
      setStatus("error")
      setErrorMessage("GitHub didn't send a valid installation_id — try installing the app again from Settings.")
      return
    }

    const id = Number(installationId)

    async function run() {
      try {
        const { account_login, account_type } = await api.installations.lookup(id)
        await api.installations.sync(
          account_type === "User" ? { scope: "me" } : { scope: "org", orgLogin: account_login },
          { account_login, account_type, installation_id: id },
        )
        setStatus("success")
        setTimeout(() => router.replace("/settings?installed=1"), 1200)
      } catch (err) {
        setStatus("error")
        setErrorMessage(err instanceof Error ? err.message : "Failed to connect the installation.")
      }
    }
    run()
  }, [searchParams, router])

  return (
    <div className="max-w-md mx-auto mt-16">
      <PageHeader title="Connecting GitHub App" description="Finishing up your GitHub App installation." />
      <div className="bg-card border border-border">
        <div className="p-4 flex flex-col gap-3">
          {status === "working" && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <CircleNotch className="size-3.5 animate-spin" /> Connecting your installation…
            </div>
          )}
          {status === "pending-approval" && (
            <p className="text-sm text-muted-foreground">
              This installation needs approval from an organization owner before it can be connected.
              Once approved, revisit Settings and it will show up automatically.
            </p>
          )}
          {status === "success" && (
            <p className="text-sm text-primary flex items-center gap-1.5">
              <CheckCircle className="size-3.5" /> Connected — redirecting to Settings…
            </p>
          )}
          {status === "error" && (
            <>
              <p className="text-sm text-destructive flex items-center gap-1.5">
                <Warning className="size-3.5" /> {errorMessage}
              </p>
              <p className="text-xs text-muted-foreground">
                If this was a brand-new organization, sign out and back in with GitHub to refresh your admin
                status, then retry the install from Settings.
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
