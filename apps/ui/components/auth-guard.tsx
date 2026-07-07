"use client"

import { useEffect } from "react"
import { useRouter, usePathname } from "next/navigation"
import { useAuth } from "@/lib/auth-context"
import { api } from "@/lib/api/client"

// Routes that don't require authentication
const PUBLIC_ROUTES = ["/login", "/setup", "/register"]

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, isLoading } = useAuth()
  const router = useRouter()
  const pathname = usePathname()

  const isPublic = PUBLIC_ROUTES.includes(pathname)

  useEffect(() => {
    if (isLoading) return

    if (isPublic) {
      // Already on a public route — no redirect needed
      return
    }

    if (!user) {
      // Check if setup is needed before redirecting to /login or /setup
      api.auth.setupRequired()
        .then(({ setup_required }) => {
          router.replace(setup_required ? "/setup" : "/login")
        })
        .catch(() => {
          router.replace("/login")
        })
    }
  }, [isLoading, user, isPublic, router])

  // Listen for 401 events from the API client
  useEffect(() => {
    function handle401() {
      router.replace("/login")
    }
    window.addEventListener("clevis:unauthorized", handle401)
    return () => window.removeEventListener("clevis:unauthorized", handle401)
  }, [router])

  // Show nothing while checking auth or redirecting
  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="w-4 h-4 border border-primary/40 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  // Public routes always render
  if (isPublic) return <>{children}</>

  // Protected routes: show spinner while redirect to /login or /setup is in flight
  if (!user) return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="w-4 h-4 border border-primary/40 border-t-primary rounded-full animate-spin" />
    </div>
  )

  return <>{children}</>
}
