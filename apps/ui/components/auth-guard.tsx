"use client"

import { Fragment, useEffect, useRef, useState } from "react"
import Link from "next/link"
import { useRouter, usePathname } from "next/navigation"
import { X } from "@phosphor-icons/react"
import { useAuth } from "@/lib/auth-context"
import { api } from "@/lib/api/client"
import { useMyInvitations } from "@/lib/use-my-invitations"
import { isPublicRoute } from "@/lib/public-routes"
import { currentLocationNext, withNext } from "@/lib/safe-next"

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, isLoading, logout, authUnconfirmed, pendingInvitations, dismissPendingInvitations } = useAuth()
  const router = useRouter()
  const pathname = usePathname()

  const isPublic = isPublicRoute(pathname)
  const [mineDismissed, setMineDismissed] = useState(false)
  const myInvitations = useMyInvitations(!!user, pathname)

  useEffect(() => {
    if (isLoading) return

    if (isPublic) {
      return
    }

    if (!user) {
      api.auth.setupRequired()
        .then(({ setup_required }) => {
          router.replace(setup_required ? "/setup" : withNext("/login", currentLocationNext()))
        })
        .catch(() => {
          router.replace(withNext("/login", currentLocationNext()))
        })
    }
  }, [isLoading, user, isPublic, router])

  // A ref tracks isPublic so the 401 listener reads it fresh without re-subscribing.
  const isPublicRef = useRef(isPublic)
  useEffect(() => {
    isPublicRef.current = isPublic
  }, [isPublic])

  useEffect(() => {
    function handle401() {
      // A stale token can still get attached to a best-effort call on a public route (e.g.
      // an invite preview); that must not force-log-out someone who was never logged in here.
      if (isPublicRef.current) return
      // Read the location before logout() can re-render: the user returns to where the session died.
      const loginUrl = withNext("/login", currentLocationNext())
      logout()
      router.replace(loginUrl)
    }
    window.addEventListener("clevis:unauthorized", handle401)
    return () => window.removeEventListener("clevis:unauthorized", handle401)
  }, [logout, router])

  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-4 h-4 border border-primary/40 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  if (isPublic) return <>{children}</>

  if (!user) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="w-4 h-4 border border-primary/40 border-t-primary rounded-full animate-spin" />
    </div>
  )

  return (
    <>
      {authUnconfirmed && (
        <div className="bg-yellow-500/10 border-b border-yellow-500/30 px-4 py-1.5 text-center text-xs text-yellow-400">
          Couldn&rsquo;t confirm your session with the server — your account info may be out of date until it&rsquo;s reachable again.
        </div>
      )}
      {myInvitations.length > 0 && !mineDismissed && (
        <div className="bg-primary/10 border-b border-primary/30 px-4 py-2 flex items-center justify-center gap-3 text-xs text-primary flex-wrap">
          <span>You have a pending invite:</span>
          {myInvitations.map((inv) => (
            <Link key={inv.token} href={`/invite/${inv.token}`} className="underline font-medium">
              Accept invitation to {inv.org_login}
            </Link>
          ))}
          <button
            onClick={() => setMineDismissed(true)}
            className="text-primary/60 hover:text-primary"
            aria-label="Dismiss"
          >
            <X className="size-3" />
          </button>
        </div>
      )}
      {myInvitations.length === 0 && pendingInvitations.length > 0 && (
        <div className="bg-primary/10 border-b border-primary/30 px-4 py-2 flex items-center justify-center gap-3 text-xs text-primary flex-wrap">
          <span>
            {/* Deliberately not a link: the accept token isn't exposed here. */}
            You have a pending invite to join{" "}
            {pendingInvitations.map((inv) => inv.org_login).join(", ")} — verify your email, then use your invite link, or
            ask an org admin to resend it.
          </span>
          <button
            onClick={dismissPendingInvitations}
            className="text-primary/60 hover:text-primary"
            aria-label="Dismiss"
          >
            <X className="size-3" />
          </button>
        </div>
      )}
      {/* Remount on user change: live QueryObservers memoize their last result, so a query
          keyed on `org` alone would otherwise paint the previous user's data for one frame. */}
      <Fragment key={user.id}>{children}</Fragment>
    </>
  )
}
