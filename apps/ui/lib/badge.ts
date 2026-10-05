const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8080").replace(/\/+$/, "")

/** Public, unauthenticated URL of an org's score badge. */
export function badgeUrl(orgLogin: string): string {
  return `${API_BASE}/badges/${encodeURIComponent(orgLogin)}/score.svg`
}

export function badgeMarkdown(orgLogin: string): string {
  return `![Clevis security score](${badgeUrl(orgLogin)})`
}

export function badgeHtml(orgLogin: string): string {
  return `<img src="${badgeUrl(orgLogin)}" alt="Clevis security score" />`
}
