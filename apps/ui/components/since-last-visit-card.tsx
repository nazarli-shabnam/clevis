"use client"

import Link from "next/link"
import { useNotifications } from "@/lib/use-notifications"
import type { NotificationItem } from "@/lib/api/types"

const NOUN: Record<NotificationItem["kind"], [string, string]> = {
  critical_alert: ["new critical alert", "new critical alerts"],
  score_drop: ["score drop", "score drops"],
  job_failed: ["failed job", "failed jobs"],
  permission_drift: ["permission notice", "permission notices"],
}

/** Overview summary of what changed since the user last marked notifications read. Renders nothing when
 * there is nothing unread (or the feed is unavailable), so it only takes space when it has news. */
export function SinceLastVisitCard() {
  const { items, unread } = useNotifications()
  if (unread === 0) return null

  const counts = new Map<NotificationItem["kind"], number>()
  for (const n of items) if (!n.read) counts.set(n.kind, (counts.get(n.kind) ?? 0) + 1)
  const parts = [...counts.entries()].map(([kind, n]) => `${n} ${NOUN[kind][n === 1 ? 0 : 1]}`)

  return (
    <div className="card mb-6 px-4 py-3 flex items-center justify-between gap-3 text-sm">
      <span>
        <span className="font-medium">Since you last checked:</span>{" "}
        <span className="text-muted-foreground">{parts.join(", ")}</span>
      </span>
      <Link href={items.find((n) => !n.read)?.href ?? "/security"} className="text-xs text-primary hover:underline shrink-0">
        Review
      </Link>
    </div>
  )
}
