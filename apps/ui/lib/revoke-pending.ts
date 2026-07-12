/** Track multiple in-flight revoke mutations without clobbering earlier rows. */

export function addRevokingId(ids: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(ids)
  next.add(id)
  return next
}

export function removeRevokingId(ids: ReadonlySet<number>, id: number): Set<number> {
  const next = new Set(ids)
  next.delete(id)
  return next
}

export function isRevoking(ids: ReadonlySet<number>, id: number): boolean {
  return ids.has(id)
}
