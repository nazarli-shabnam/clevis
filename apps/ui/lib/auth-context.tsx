"use client"

import { createContext, useCallback, useContext, useEffect, useState } from "react"

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
  login(email: string, password: string): Promise<void>
  logout(): void
  updateUser(u: Partial<AuthUser>): void
  setSession(jwtToken: string, authUser: AuthUser): void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const _TOKEN_KEY = "clevis:token"
const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080"

function parseJwtPayload(token: string): AuthUser | null {
  try {
    // JWT uses base64url — normalize to standard base64 before atob()
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

  const logout = useCallback(() => {
    // Clear the httpOnly cookie server-side (GitHub OAuth sessions); harmless for Bearer sessions.
    fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "include" }).catch(() => {})
    localStorage.removeItem(_TOKEN_KEY)
    setToken(null)
    setUser(null)
  }, [])

  // On mount: restore a Bearer session optimistically, then validate against the server.
  // Validation works for BOTH auth modes — the Bearer header (email/password) and the httpOnly
  // session cookie (GitHub OAuth, where there is no localStorage token).
  useEffect(() => {
    const stored = localStorage.getItem(_TOKEN_KEY)

    // Optimistic restore from a stored token — unblock the UI without a network round-trip
    if (stored) {
      const optimistic = parseJwtPayload(stored)
      if (optimistic) {
        setToken(stored)
        setUser(optimistic)
        setIsLoading(false)
      }
    }

    // Timed out so a stalled /auth/me can never leave the app stuck on the full-screen spinner.
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    fetch(`${BASE}/auth/me`, {
      headers: stored ? { Authorization: `Bearer ${stored}` } : {},
      credentials: "include", // sends the httpOnly cookie for GitHub OAuth sessions
      signal: controller.signal,
    })
      .then(async (res) => {
        if (res.status === 401) {
          if (stored) logout()
          return
        }
        if (!res.ok) return
        const data = (await res.json()) as AuthUser
        if (stored) setToken(stored)
        setUser(data)
      })
      .catch(() => {
        // Network unreachable or timed out — keep any optimistic session
      })
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
    const { access_token, user: u } = data as { access_token: string; user: AuthUser }
    localStorage.setItem(_TOKEN_KEY, access_token)
    setToken(access_token)
    setUser(u)
  }, [])

  const updateUser = useCallback((patch: Partial<AuthUser>) => {
    setUser((prev) => (prev ? { ...prev, ...patch } : prev))
  }, [])

  const setSession = useCallback((jwtToken: string, authUser: AuthUser) => {
    localStorage.setItem(_TOKEN_KEY, jwtToken)
    setToken(jwtToken)
    setUser(authUser)
  }, [])

  return (
    <AuthContext.Provider value={{ user, token, isLoading, login, logout, updateUser, setSession }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
