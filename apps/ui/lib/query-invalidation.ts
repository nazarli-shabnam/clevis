import type { QueryClient } from "@tanstack/react-query"

// A saved or removed token changes Settings' list and every resolved-token lookup (Overview's
// "configured", the repo/security/automation auto-fill). The two keys have different first
// segments, so invalidating one prefix misses the other.
export function invalidateTokens(queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: ["tokens"] })
  queryClient.invalidateQueries({ queryKey: ["tokens.resolve"] })
}

// Installation data is cached under three first segments (personal list, per-org list, permission
// view); after a connect/disconnect/re-sync all of them can be stale, not just the one that was read.
export function invalidateInstallations(queryClient: QueryClient) {
  for (const key of ["installations", "installations.org", "installations.permissions"]) {
    queryClient.invalidateQueries({ queryKey: [key] })
  }
}

// Everything a finished scan makes stale for an org: the stored scan, its history, the "what changed" diff, the
// scan list and the cockpit (Overview's score card and the sidebar health dot read the cockpit's latest score,
// which otherwise lags by its 30 s refetch interval). One list for every place that runs a scan.
export function invalidateScanData(queryClient: QueryClient, owner: string) {
  for (const key of [
    "analytics.latest-scan",
    "analytics.history",
    "analytics.changes",
    "analytics.scans",
    "analytics.cockpit",
  ]) {
    queryClient.invalidateQueries({ queryKey: [key, owner] })
  }
}
