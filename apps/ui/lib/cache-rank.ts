import { classifyStaleness } from "@/lib/format"
import type { CacheEntry } from "@/lib/api/types"

export interface RepoCacheStats {
  repo: string
  count: number
  totalBytes: number
  /** Bytes in entries not accessed for 7+ days (`classifyStaleness` "stale" or "old"). */
  staleBytes: number
  /** Most recent `last_accessed_at` across the repo's entries; null when it has none. */
  lastAccessed: string | null
}

export function summarizeCaches(repo: string, entries: CacheEntry[]): RepoCacheStats {
  let totalBytes = 0
  let staleBytes = 0
  let lastAccessed: string | null = null
  for (const e of entries) {
    totalBytes += e.size_in_bytes
    if (classifyStaleness(e.last_accessed_at) !== "fresh") staleBytes += e.size_in_bytes
    // Compare instants, not strings: the API may format the same moment differently.
    if (lastAccessed === null || Date.parse(e.last_accessed_at) > Date.parse(lastAccessed)) lastAccessed = e.last_accessed_at
  }
  return { repo, count: entries.length, totalBytes, staleBytes, lastAccessed }
}

/** Repos holding cache, biggest first; `staleOnly` keeps those with at least one stale entry and
 * ranks them by their stale bytes, since that is what a cleanup would reclaim. */
export function rankByCacheSize(rows: RepoCacheStats[], staleOnly: boolean): RepoCacheStats[] {
  const key = (r: RepoCacheStats) => (staleOnly ? r.staleBytes : r.totalBytes)
  return rows
    .filter((r) => r.count > 0 && (!staleOnly || r.staleBytes > 0))
    .sort((a, b) => key(b) - key(a) || a.repo.localeCompare(b.repo))
}
