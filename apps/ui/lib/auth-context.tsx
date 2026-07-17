"use client"

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react"
import type { PendingInvitationSummary } from "@/lib/api/types"

export interface AuthUser {
  id: number
  email: string
  name: string | null
  is_workspace_admin: boolean
}

interface AuthContextValue {
  user: AuthUser | null
  token: string | null
  isLoading: boolean
  logoutWarning: string | null
  pendingInvitations: PendingInvitationSummary[]
  login(email: string, password: string): Promise<void>
  logout(): void
  clearLogoutWarning(): void
  updateUser(u: Partial<AuthUser>): void
  setSession(jwtToken: string, authUser: AuthUser, pendingInvitations?: PendingInvitationSummary[]): void
  dismissPendingInvitations(): void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const _TOKEN_KEY = "clevis:token"
const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080"
const _LOGOUT_WARNING =
  "Logged out locally, but the server session may still be active. Avoid shared devices until you can retry."

function parseJwtPayload(token: string): AuthUser | null {
  try {
    const segment = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")
    const padded = segment.padEnd(Math.ceil(segment.length / 4) * 4, "=")
    const payload = JSON.parse(atob(padded))
    const id = Number(payload.sub)
    if (!id || !payload.email) return null
    if (payload.exp && payload.exp * 1000 < Date.now()) return null
    return {
      id,
      email: payload.email,
      name: payload.name ?? null,
      is_workspace_admin: Boolean(payload.is_workspace_admin),
    }
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [logoutWarning, setLogoutWarning] = useState<string | null>(null)
  const [pendingInvitations, setPendingInvitations] = useState<PendingInvitationSummary[]>([])
  const sessionEpochRef = useRef(0)

  const dismissPendingInvitations = useCallback(() => {
    setPendingInvitations([])
  }, [])

  const bumpSessionEpoch = useCallback(() => {
    sessionEpochRef.current += 1
  }, [])

  const clearLogoutWarning = useCallback(() => {
    setLogoutWarning(null)
  }, [])

  const logout = useCallback(() => {
    bumpSessionEpoch()
    fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "include" })
      .then((res) => {
        if (!res.ok) setLogoutWarning(_LOGOUT_WARNING)
      })
      .catch(() => setLogoutWarning(_LOGOUT_WARNING))
    localStorage.removeItem(_TOKEN_KEY)
    setToken(null)
    setUser(null)
    setPendingInvitations([])
  }, [bumpSessionEpoch])

  useEffect(() => {
    const epochAtStart = sessionEpochRef.current
    const stored = localStorage.getItem(_TOKEN_KEY)

    if (stored) {
      const optimistic = parseJwtPayload(stored)
      if (optimistic) {
        setToken(stored)
        setUser(optimistic)
        setIsLoading(false)
      }
    }

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    fetch(`${BASE}/auth/me`, {
      headers: stored ? { Authorization: `Bearer ${stored}` } : {},
      credentials: "include",
      signal: controller.signal,
    })
      .then(async (res) => {
        if (sessionEpochRef.current !== epochAtStart) return
        if (res.status === 401) {
          if (stored) logout()
          return
        }
        if (!res.ok) return
        const data = (await res.json()) as AuthUser
        if (stored) setToken(stored)
        setUser(data)
      })
      .catch(() => {})
      .finally(() => {
        clearTimeout(timer)
        setIsLoading(false)
      })
  }, [logout])

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail ?? "Login failed")
    const { access_token, user: u, pending_invitations } = data as {
      access_token: string
      user: AuthUser
      pending_invitations?: PendingInvitationSummary[]
    }
    bumpSessionEpoch()
    clearLogoutWarning()
    localStorage.setItem(_TOKEN_KEY, access_token)
    setToken(access_token)
    setUser(u)
    setPendingInvitations(pending_invitations ?? [])
  }, [bumpSessionEpoch, clearLogoutWarning])

  const updateUser = useCallback((patch: Partial<AuthUser>) => {
    setUser((prev) => (prev ? { ...prev, ...patch } : prev))
  }, [])

  const setSession = useCallback(
    (jwtToken: string, authUser: AuthUser, invitations: PendingInvitationSummary[] = []) => {
      bumpSessionEpoch()
      clearLogoutWarning()
      localStorage.setItem(_TOKEN_KEY, jwtToken)
      setToken(jwtToken)
      setUser(authUser)
      setPendingInvitations(invitations)
    },
    [bumpSessionEpoch, clearLogoutWarning],
  )

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        isLoading,
        logoutWarning,
        pendingInvitations,
        login,
        logout,
        clearLogoutWarning,
        updateUser,
        setSession,
        dismissPendingInvitations,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
