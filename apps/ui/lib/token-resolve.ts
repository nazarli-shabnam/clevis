/** Guard token auto-resolve so stale responses cannot bind the wrong org. */

export function shouldApplyResolvedToken(requestedOrg: string, currentOwner: string): boolean {
  return requestedOrg.trim() === currentOwner.trim()
}
