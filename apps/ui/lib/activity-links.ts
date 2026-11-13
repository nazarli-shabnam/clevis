import { githubWebUrl } from "@/lib/github-web"

/** Clevis repo page for a "owner/name" repo as the activity endpoints report it; null if it isn't in that shape. */
export function repoPageHref(fullName: string): string | null {
  const [owner, name, ...rest] = fullName.split("/")
  if (!owner || !name || rest.length > 0) return null
  return `/repos/${encodeURIComponent(owner)}~${encodeURIComponent(name)}`
}

/** GitHub URL of the pull request or issue an event summary names ("opened PR #42: …", "closed issue #7: …"),
 * or null when it names none. The events carry the number only inside their summary text. */
export function eventItemHref(repo: string, summary: string): { href: string; label: string } | null {
  const match = /\b(PR|issue) #(\d+)/.exec(summary)
  if (!match || !repoPageHref(repo)) return null
  const [, kind, number] = match
  return { href: githubWebUrl(`${repo}/${kind === "PR" ? "pull" : "issues"}/${number}`), label: `#${number}` }
}
