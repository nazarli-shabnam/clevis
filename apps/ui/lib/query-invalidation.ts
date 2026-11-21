import type { QueryClient } from "@tanstack/react-query"

// A saved or removed token changes the saved-token list (Settings and the sidebar's scope picker share the
// ["tokens"] key) and, because saving a PAT also connects the org server-side, the user's org memberships.
export function invalidateTokens(queryClient: QueryClient) {
  queryClient.invalidateQueries({ queryKey: ["tokens"] })
  queryClient.invalidateQueries({ queryKey: ["my-orgs"] })
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
