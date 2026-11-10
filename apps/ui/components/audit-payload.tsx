"use client"

import { CopyButton } from "@/components/copy-button"
import { payloadEntries, prettyPayload } from "@/lib/audit-payload"

// Long values (a free-text title, a list of repos) are cut in the display; the copy button has them whole.
const MAX_VALUE_CHARS = 240

function shorten(value: string): string {
  return value.length > MAX_VALUE_CHARS ? `${value.slice(0, MAX_VALUE_CHARS)}…` : value
}

/** The stored detail of one audit entry as key/value pairs. */
export function AuditPayload({ payload }: { payload: string }) {
  const entries = payloadEntries(payload)
  return (
    <div className="flex items-start justify-between gap-4">
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs min-w-0">
        {entries.map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="font-mono text-muted-foreground">{key}</dt>
            <dd className="font-mono text-foreground/80 break-all" title={value.length > MAX_VALUE_CHARS ? value : undefined}>
              {shorten(value)}
            </dd>
          </div>
        ))}
      </dl>
      <CopyButton value={prettyPayload(payload)} ariaLabel="Copy audit entry details" />
    </div>
  )
}
