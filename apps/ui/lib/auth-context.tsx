"use client"

import { createContext, useCallback, useContext, useEffect, useState } from "react"

export interface AuthUser {
  id: number
  email: string
  name: string | null
  is_owner: boolean
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
    return { id, email: payload.email, name: payload.name ?? null, is_owner: Boolean(payload.is_owner) }
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const logout = useCallback(() => {
    localStorage.removeItem(_TOKEN_KEY)
    setToken(null)
    setUser(null)
  }, [])

  // On mount: restore session from JWT immediately, then validate silently in background
  useEffect(() => {
    const stored = localStorage.getItem(_TOKEN_KEY)
    if (!stored) {
      setIsLoading(false)
      return
    }

    // Optimistic restore — unblock the UI without a network round-trip
    const optimistic = parseJwtPayload(stored)
    if (optimistic) {
      setToken(stored)
      setUser(optimistic)
      setIsLoading(false)
    }

    // Background validation — evict stale tokens, refresh user fields
    fetch(`${BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${stored}` },
    })
      .then(async (res) => {
        if (res.status === 401) {
          logout()
          return
        }
        if (!res.ok) return
        const data = await res.json()
        setToken(stored)
        setUser(data as AuthUser)
      })
      .catch(() => {
        // Network unreachable — keep optimistic session
      })
      .finally(() => setIsLoading(false))
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
