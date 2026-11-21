import { useState } from "react"

/** Run `run` for every id, a few at a time; resolves with the ids that failed (and why) instead of throwing,
 * so one bad row never hides how the rest went. */
export async function runBulk<T extends string | number>(
  ids: readonly T[],
  run: (id: T) => Promise<unknown>,
  batchSize = 5,
): Promise<{ id: T; message: string }[]> {
  const failed: { id: T; message: string }[] = []
  for (let i = 0; i < ids.length; i += batchSize) {
    const batch = ids.slice(i, i + batchSize)
    const results = await Promise.allSettled(batch.map(run))
    results.forEach((r, j) => {
      if (r.status === "rejected") failed.push({ id: batch[j], message: r.reason instanceof Error ? r.reason.message : "request failed" })
    })
  }
  return failed
}

/** Row selection for a list. What can be acted on is always the selected ids that are still `visibleIds`, so a
 * filter change or a row that disappeared can never leave a hidden row in a bulk action. */
export function useRowSelection<T extends string | number>(visibleIds: readonly T[]) {
  const [selected, setSelected] = useState<ReadonlySet<T>>(new Set())
  const chosen = visibleIds.filter((id) => selected.has(id))
  const allSelected = visibleIds.length > 0 && chosen.length === visibleIds.length
  return {
    chosen,
    allSelected,
    isSelected: (id: T) => selected.has(id),
    toggle: (id: T) =>
      setSelected((prev) => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        return next
      }),
    toggleAll: () => setSelected(allSelected ? new Set() : new Set(visibleIds)),
    /** After a bulk run: keep only the rows that failed, so they can be retried. */
    keepOnly: (ids: readonly T[]) => setSelected(new Set(ids)),
  }
}
