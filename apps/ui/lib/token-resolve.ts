/** Guard token auto-resolve so stale responses cannot bind the wrong org. */

// Any non-empty login can be looked up: GitHub org logins can be a single character, so a
// length floor (> 2) would leave short orgs permanently without a resolved token.
export const hasOrgLogin = (org: string): boolean => org.trim().length > 0

export function shouldApplyResolvedToken(requestedOrg: string, currentOwner: string): boolean {
  return requestedOrg.trim() === currentOwner.trim()
}
