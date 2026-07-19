"use client"

import { relativeTime } from "@/lib/format"
import type { OrgEvent } from "@/lib/api/types"

interface EventActivityListProps {
  events: OrgEvent[]
  isLoading: boolean
  limit?: number
}

/** Sibling to ActivityList, rendering GitHub org events instead of JobOut rows. */
export function EventActivityList({ events, isLoading, limit }: EventActivityListProps) {
  if (isLoading) {
    return (
      <div className="divide-y divide-border">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="px-4 py-3 flex items-center justify-between gap-4 animate-pulse">
            <div className="flex flex-col gap-1.5">
              <div className="h-3 w-40 bg-muted rounded-md" />
              <div className="h-2.5 w-24 bg-muted/60 rounded-md" />
            </div>
            <div className="h-2.5 w-14 bg-muted/60 rounded-md" />
          </div>
        ))}
      </div>
    )
  }

  const visible = limit ? events.slice(0, limit) : events

  if (visible.length === 0) {
    return (
      <div className="px-4 py-8">
        <p className="text-sm text-muted-foreground font-mono">— no recent activity</p>
      </div>
    )
  }

  return (
    <div className="divide-y divide-border">
      {visible.map((event, i) => (
        <div
          key={event.id}
          className="stagger-item px-4 py-3 flex items-start justify-between gap-4 hover:bg-elevated transition-colors duration-150 ease-(--ease-out)"
          style={{ animationDelay: `${Math.min(i, 6) * 45}ms` }}
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm text-foreground/90 truncate">{event.actor}</span>
              <span className="text-[0.6875rem] text-muted-foreground/80 truncate">{event.summary}</span>
            </div>
            <p className="text-[0.6875rem] text-muted-foreground/60 font-mono mt-0.5 truncate">
              {event.repo}
            </p>
          </div>
          <span className="text-[0.6875rem] text-muted-foreground whitespace-nowrap shrink-0 mt-0.5">
            {relativeTime(event.created_at)}
          </span>
        </div>
      ))}
    </div>
  )
}
