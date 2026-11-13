"use client"

import { Button } from "@/components/ui/button"

/** An empty list inside a card: one line on why the thing is worth setting up, and a button that takes the
 * user to the create form that sits further down the same card (the form's first field is `focusId`). */
export function EmptyStateAction({
  message,
  actionLabel,
  focusId,
}: {
  message: string
  actionLabel: string
  focusId: string
}) {
  return (
    <div className="px-4 py-3 flex flex-wrap items-center gap-3">
      <p className="text-xs text-muted-foreground">{message}</p>
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          const field = document.getElementById(focusId)
          field?.scrollIntoView?.({ block: "center" })
          field?.focus()
        }}
      >
        {actionLabel}
      </Button>
    </div>
  )
}
