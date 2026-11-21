"use client"

import { useState } from "react"
import { useMutation } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import { runBulk } from "@/lib/bulk"

/** One button that acts on many rows after a single confirm, and reports the rows that failed. Renders nothing
 * for an empty `ids`. `onDone` gets the ids that failed so the caller can refresh and keep just those selected. */
export function BulkActionBar<T extends string | number>({
  ids,
  label,
  confirmTitle,
  confirmDescription,
  confirmLabel,
  run,
  onDone,
}: {
  ids: readonly T[]
  label: string
  confirmTitle: string
  confirmDescription: string
  confirmLabel: string
  run: (id: T) => Promise<unknown>
  onDone: (failedIds: T[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const bulk = useMutation({
    mutationFn: () => runBulk(ids, run),
    onSuccess: (failed) => {
      setOpen(false)
      setFailure(failed.length ? `${failed.length} of ${ids.length} failed: ${failed[0].message}` : null)
      onDone(failed.map((f) => f.id))
    },
  })

  if (ids.length === 0 && !failure) return null
  return (
    <>
      {ids.length > 0 && (
        <Button size="sm" variant="outline" disabled={bulk.isPending} onClick={() => setOpen(true)}>
          {label} ({ids.length})
        </Button>
      )}
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={confirmTitle}
        description={confirmDescription}
        confirmLabel={confirmLabel}
        pending={bulk.isPending}
        onConfirm={() => bulk.mutate()}
      />
      {failure && (
        <p role="alert" className="basis-full text-xs text-destructive">
          {failure}
        </p>
      )}
    </>
  )
}
