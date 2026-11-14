"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

/** Change the password of an email/password account. Every other session is signed out; this one gets a fresh
 * token so the user stays signed in. A GitHub-only account has no password, and the API says so. */
export function ChangePasswordCard() {
  const { setSession } = useAuth()
  const queryClient = useQueryClient()
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const [confirm, setConfirm] = useState("")
  const [error, setError] = useState("")
  const change = useMutation({
    mutationFn: () => api.auth.changePassword(current, next),
    onSuccess: (data) => {
      setSession(data.access_token, data.user, data.pending_invitations)
      // Other sessions were just signed out and this one replaced: the sessions list is stale.
      queryClient.invalidateQueries({ queryKey: ["auth.sessions"] })
      setCurrent("")
      setNext("")
      setConfirm("")
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Couldn't change the password"),
  })

  function submit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    if (next.length < 12) return setError("New password must be at least 12 characters.")
    if (next !== confirm) return setError("The new passwords do not match.")
    change.reset()
    change.mutate()
  }

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Password</span>
      </div>
      <form onSubmit={submit} className="p-4 grid gap-3 sm:grid-cols-3">
        <div>
          <label htmlFor="current-password" className="text-xs font-medium block mb-1.5">Current password</label>
          <Input id="current-password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="new-password" className="text-xs font-medium block mb-1.5">New password</label>
          <Input id="new-password" type="password" autoComplete="new-password" placeholder="At least 12 characters" value={next} onChange={(e) => setNext(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="confirm-password" className="text-xs font-medium block mb-1.5">Confirm new password</label>
          <Input id="confirm-password" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
        </div>
        <div className="sm:col-span-3 flex items-center gap-3">
          <Button type="submit" disabled={change.isPending}>{change.isPending ? "Changing…" : "Change password"}</Button>
          {change.isSuccess && (
            <p role="status" className="text-xs text-primary">Password changed. Your other sessions were signed out.</p>
          )}
          {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        </div>
      </form>
    </div>
  )
}
