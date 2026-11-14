"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { Input } from "@/components/ui/input"

/** Change the account email. A link goes to the NEW address and nothing changes until it is opened. */
export function ChangeEmailCard() {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const change = useMutation({
    mutationFn: async () => {
      const sentTo = email.trim()
      await api.auth.requestEmailChange(sentTo, password)
      return sentTo
    },
    onSuccess: () => setPassword(""),
  })

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Email address</span>
      </div>
      <form
        className="p-4 grid gap-3 sm:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault()
          change.mutate()
        }}
      >
        <div>
          <label htmlFor="new-email" className="text-xs font-medium block mb-1.5">New email</label>
          <Input id="new-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </div>
        <div>
          <label htmlFor="new-email-password" className="text-xs font-medium block mb-1.5">Password</label>
          <Input id="new-email-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>
        <div className="flex items-end">
          <Button type="submit" disabled={change.isPending}>{change.isPending ? "Sending…" : "Send confirmation link"}</Button>
        </div>
        <p className="sm:col-span-3 text-xs text-muted-foreground">
          We email a link to the new address; nothing changes until you open it, and you&apos;ll sign in again afterwards.
          Pending invitations stay tied to your old address.
        </p>
        {change.isSuccess && (
          <p role="status" className="sm:col-span-3 text-xs text-primary">Check {change.data} for the confirmation link.</p>
        )}
        {change.isError && (
          <p role="alert" className="sm:col-span-3 text-xs text-destructive">{change.error.message}</p>
        )}
      </form>
    </div>
  )
}

/** Delete the account. Needs the email typed back (and the password, for a password account). */
export function DeleteAccountCard() {
  const { user, logout } = useAuth()
  const [confirmEmail, setConfirmEmail] = useState("")
  const [password, setPassword] = useState("")
  const [open, setOpen] = useState(false)
  const remove = useMutation({
    mutationFn: () => api.auth.deleteAccount(confirmEmail.trim(), password || undefined),
    onSuccess: () => logout(),
    onSettled: () => setOpen(false),
  })
  const matches = !!user && confirmEmail.trim().toLowerCase() === user.email.toLowerCase()

  return (
    <div className="card">
      <div className="px-4 py-3 border-b border-border">
        <span className="section-label">Delete account</span>
      </div>
      <div className="p-4 grid gap-3 sm:grid-cols-3">
        <p className="sm:col-span-3 text-xs text-muted-foreground">
          Removes your account, your sessions, your memberships and your personal data. Organization history keeps your
          email as the author of past actions. You can&apos;t delete the workspace admin account, or an account that is
          the only admin of an organization.
        </p>
        <div>
          <label htmlFor="delete-confirm-email" className="text-xs font-medium block mb-1.5">Type your email to confirm</label>
          <Input id="delete-confirm-email" type="email" value={confirmEmail} onChange={(e) => setConfirmEmail(e.target.value)} />
        </div>
        <div>
          <label htmlFor="delete-password" className="text-xs font-medium block mb-1.5">Password (if you have one)</label>
          <Input id="delete-password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div className="flex items-end">
          <Button variant="destructive" disabled={!matches || remove.isPending} onClick={() => setOpen(true)}>
            Delete my account
          </Button>
        </div>
        {remove.isError && (
          <p role="alert" className="sm:col-span-3 text-xs text-destructive">Couldn&apos;t delete: {remove.error.message}</p>
        )}
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Delete your account?"
        description="This can't be undone."
        confirmLabel="Delete"
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
      />
    </div>
  )
}
