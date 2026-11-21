import { dueTime, exactTime, relativeTime } from "@/lib/format"

/** A relative time ("3d ago", or "in 3 days" with `due`) whose exact timestamp shows on hover. */
export function Time({ iso, due }: { iso: string; due?: boolean }) {
  return (
    <time dateTime={iso} title={exactTime(iso)}>
      {due ? dueTime(iso) : relativeTime(iso)}
    </time>
  )
}
