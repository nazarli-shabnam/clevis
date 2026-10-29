"use client"

import Link from "next/link"
import { useState } from "react"
import { Popover } from "@base-ui/react/popover"
import { Bell, GearSix } from "@phosphor-icons/react"
import { relativeTime } from "@/lib/format"
import type { NotificationKind } from "@/lib/api/types"
import { useNotificationPreferences, useNotifications } from "@/lib/use-notifications"

const KIND_LABEL = {
  critical_alert: "Critical alert",
  score_drop: "Score drop",
  job_failed: "Job failed",
  permission_drift: "Permissions",
  waiver_expired: "Waiver expired",
} as const

const KIND_ORDER: NotificationKind[] = ["critical_alert", "score_drop", "job_failed", "permission_drift", "waiver_expired"]
const KIND_HINT: Record<NotificationKind, string> = {
  critical_alert: "New critical Dependabot alerts",
  score_drop: "Security score drops",
  job_failed: "Failed background jobs (admins)",
  permission_drift: "GitHub App permission changes (admins)",
  waiver_expired: "Check waivers that expired (admins)",
}

/** Per-user switches for which kinds appear in the bell. Loads (and saves) only while shown. */
function PreferencesPanel({ org }: { org: string }) {
  const { query, save, muted } = useNotificationPreferences(org, true)
  const toggle = (kind: NotificationKind, show: boolean) =>
    save.mutate(show ? muted.filter((k) => k !== kind) : [...muted, kind])
  return (
    <div className="border-b border-border px-3 py-2">
      <p className="mb-1 text-xs text-muted-foreground">Show these in your notifications:</p>
      {query.isError ? (
        <p role="alert" className="text-xs text-destructive">
          Couldn&apos;t load your settings: {query.error.message}
        </p>
      ) : (
        <ul className="space-y-1">
          {KIND_ORDER.map((kind) => (
            <li key={kind}>
              <label className="flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={!muted.includes(kind)}
                  disabled={!query.isSuccess || save.isPending}
                  onChange={(e) => toggle(kind, e.target.checked)}
                />
                {KIND_HINT[kind]}
              </label>
            </li>
          ))}
        </ul>
      )}
      {save.isError && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          Couldn&apos;t save: {save.error.message}
        </p>
      )}
    </div>
  )
}

/** Header bell for the active org's notifications, with an unread badge and a mark-all-read action. */
export function NotificationBell() {
  const { org, query, markRead, items, unread } = useNotifications()
  const [open, setOpen] = useState(false)
  const [customizing, setCustomizing] = useState(false)

  if (!org) return null

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        className="relative flex items-center rounded-md border border-border/60 p-1.5 text-muted-foreground hover:text-foreground hover:bg-elevated transition-colors"
      >
        <Bell className="size-3.5" />
        {unread > 0 && (
          <span
            aria-hidden
            className="absolute -top-1.5 -right-1.5 min-w-4 rounded-full bg-primary px-1 text-center text-[0.625rem] font-medium leading-4 text-primary-foreground tabular-nums"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Popover.Popup className="w-[min(24rem,calc(100vw-2rem))] rounded-lg border border-border bg-popover text-popover-foreground shadow-xl outline-none">
            <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
              <Popover.Title className="text-sm font-medium">Notifications · {org}</Popover.Title>
              <button
                type="button"
                onClick={() => setCustomizing((v) => !v)}
                aria-label="Notification settings"
                aria-expanded={customizing}
                className="ml-auto text-muted-foreground hover:text-foreground"
              >
                <GearSix className="size-3.5" />
              </button>
              <button
                type="button"
                onClick={() => markRead.mutate()}
                disabled={unread === 0 || markRead.isPending}
                className="text-xs text-primary hover:underline disabled:text-muted-foreground disabled:no-underline"
              >
                Mark all read
              </button>
            </div>
            {customizing && <PreferencesPanel org={org} />}
            {query.isError ? (
              <p role="alert" className="px-3 py-4 text-xs text-destructive">
                Couldn&apos;t load notifications: {query.error.message}
              </p>
            ) : query.isLoading ? (
              <p className="px-3 py-4 text-xs text-muted-foreground">Loading…</p>
            ) : items.length === 0 ? (
              <p className="px-3 py-4 text-xs text-muted-foreground">Nothing new in the last 14 days.</p>
            ) : (
              <ul className="max-h-96 divide-y divide-border overflow-y-auto">
                {items.map((n) => (
                  <li key={n.id}>
                    <Link
                      href={n.href}
                      onClick={() => setOpen(false)}
                      className={`flex flex-col gap-0.5 px-3 py-2 text-xs hover:bg-elevated ${n.read ? "text-muted-foreground" : "text-foreground"}`}
                    >
                      <span className="flex items-center gap-2">
                        {!n.read && <span aria-label="Unread" className="size-1.5 shrink-0 rounded-full bg-primary" />}
                        <span className="font-medium">{n.title}</span>
                      </span>
                      {n.detail && <span className="truncate text-muted-foreground">{n.detail}</span>}
                      <span className="text-[0.6875rem] text-muted-foreground">
                        {KIND_LABEL[n.kind]} · {relativeTime(n.at)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
            {markRead.isError && (
              <p role="alert" className="border-t border-border px-3 py-2 text-xs text-destructive">
                Couldn&apos;t mark as read: {markRead.error.message}
              </p>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}
