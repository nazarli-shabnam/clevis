// Only accept unambiguous same-site relative paths: reject protocol-relative ("//..."),
// backslash variants (some browsers normalize "/\..." to "//..."), and control characters
// that could let URL parsing reinterpret this as an external navigation target.
export function safeNextPath(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//")) return "/"
  if (next.includes("\\") || /[\x00-\x1f\x7f]/.test(next)) return "/"
  return next
}

// `path` with the (already sanitised) post-login destination attached; "/" is the default so it is omitted.
export function withNext(path: string, next: string): string {
  return next !== "/" ? `${path}?next=${encodeURIComponent(next)}` : path
}

// The page the user is on right now, as a sanitised "?next=" target for the login page.
export function currentLocationNext(): string {
  return safeNextPath(`${window.location.pathname}${window.location.search}`)
}
