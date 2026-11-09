"use client"

import Link from "next/link"
import { useState } from "react"
import { PageHeader } from "@/components/page-header"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { SectionError } from "@/components/section-error"
import { EmptyStateNoAccount } from "@/components/empty-state"
import { useActiveScope } from "@/lib/active-scope"
import { relativeTime } from "@/lib/format"
import { KIND_LABEL, KIND_ORDER } from "@/lib/notification-kinds"
import { PreferencesPanel } from "@/components/notification-bell"
import { useNotifications } from "@/lib/use-notifications"
import type { NotificationKind } from "@/lib/api/types"

// Mirrors the API's window for the full feed (`days`).
const FULL_FEED_DAYS = 90

export default function NotificationsPage() {
  const { scope } = useActiveScope()
  const { org, query, markRead, dismiss, items, unread, hasMore, loadingMore, loadMore } = useNotifications({ full: true })
  const [customizing, setCustomizing] = useState(false)
  // Kinds shown; all by default. Client-side: the feed is already one bounded request, and the per-user
  // mute preferences (in the bell) are what hide a kind server-side.
  const [shown, setShown] = useState<Set<NotificationKind>>(new Set(KIND_ORDER))
  const [unreadOnly, setUnreadOnly] = useState(false)

  const toggle = (kind: NotificationKind) =>
    setShown((prev) => {
      const next = new Set(prev)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })

  const visible = items.filter((n) => shown.has(n.kind) && (!unreadOnly || !n.read))
  const present = new Set(items.map((n) => n.kind))

  return (
    <>
      <PageHeader title="Notifications" description={`Everything that changed${org ? ` in ${org}` : ""} in the last ${FULL_FEED_DAYS} days.`} />
      <div className="card">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap" role="group" aria-label="Filter by kind">
            {KIND_ORDER.map((kind) => (
              <button
                key={kind}
                type="button"
                aria-pressed={shown.has(kind)}
                onClick={() => toggle(kind)}
                className={`text-xs font-medium px-2.5 py-1 rounded-md border transition-colors ${
                  shown.has(kind) ? "border-border bg-elevated text-foreground" : "border-transparent text-muted-foreground hover:bg-elevated"
                }`}
              >
                {KIND_LABEL[kind]}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
              Unread only
            </label>
            {org && (
              <Button size="sm" variant="ghost" aria-expanded={customizing} onClick={() => setCustomizing((v) => !v)}>
                Preferences
              </Button>
            )}
            <Button size="sm" variant="outline" disabled={unread === 0 || markRead.isPending} onClick={() => markRead.mutate()}>
              Mark all read
            </Button>
          </div>
        </div>
        {customizing && org && <PreferencesPanel org={org} />}
        {markRead.isError && (
          <p role="alert" className="px-4 py-2 text-xs text-destructive border-b border-border">
            Couldn&apos;t mark as read: {markRead.error.message}
          </p>
        )}
        {dismiss.isError && (
          <p role="alert" className="px-4 py-2 text-xs text-destructive border-b border-border">
            Couldn&apos;t mark &ldquo;{items.find((n) => n.id === dismiss.variables)?.title ?? "that notification"}&rdquo; as read: {dismiss.error.message}
          </p>
        )}
        {!org ? (
          <EmptyStateNoAccount bare message={scope?.kind === "personal" ? "Notifications belong to an organization. Pick an organization from the profile menu." : undefined} />
        ) : query.isError ? (
          <SectionError message={query.error.message} onRetry={() => query.refetch()} retrying={query.isFetching} />
        ) : query.isLoading ? (
          <div className="p-4 flex flex-col gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <p className="px-4 py-8 text-sm text-muted-foreground">Nothing in the last {FULL_FEED_DAYS} days.</p>
        ) : visible.length === 0 ? (
          <p className="px-4 py-8 text-sm text-muted-foreground">
            No notifications match these filters
            {[...present].every((k) => !shown.has(k)) ? ": every kind that has items is switched off." : "."}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {visible.map((n) => (
              <li key={n.id} className="flex items-start gap-3 px-4 py-2.5">
                <Link href={n.href} className={`flex min-w-0 flex-1 flex-col gap-0.5 text-xs ${n.read ? "text-muted-foreground" : "text-foreground"}`}>
                  <span className="flex items-center gap-2">
                    {!n.read && <span aria-label="Unread" className="size-1.5 shrink-0 rounded-full bg-primary" />}
                    <span className="font-medium">{n.title}</span>
                  </span>
                  {n.detail && <span className="truncate text-muted-foreground">{n.detail}</span>}
                  <span className="text-[0.6875rem] text-muted-foreground">
                    {KIND_LABEL[n.kind]} · {relativeTime(n.at)}
                  </span>
                </Link>
                {!n.read && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Mark "${n.title}" as read`}
                    disabled={dismiss.isPending && dismiss.variables === n.id}
                    onClick={() => dismiss.mutate(n.id)}
                  >
                    Mark read
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {hasMore && (
          <div className="px-4 py-3 border-t border-border">
            <Button size="sm" variant="outline" disabled={loadingMore} onClick={loadMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        )}
      </div>
    </>
  )
}
