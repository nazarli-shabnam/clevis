// Whether `address` could be the one behind a masked invitation address ("a***@acme.com", as the API
// returns it): same first letter and same domain, case-insensitively. Only the masked form reaches the
// browser, so a match means "possibly this account", but a mismatch means it certainly isn't.
export function matchesMaskedEmail(masked: string, address: string): boolean {
  const [maskedLocal, maskedDomain] = splitAddress(masked)
  const [local, domain] = splitAddress(address)
  return maskedLocal.slice(0, 1) === local.slice(0, 1) && maskedDomain === domain
}

// Split at the first "@", like the API's masking does.
function splitAddress(address: string): [string, string] {
  const lower = address.trim().toLowerCase()
  const at = lower.indexOf("@")
  return at === -1 ? [lower, ""] : [lower.slice(0, at), lower.slice(at + 1)]
}
