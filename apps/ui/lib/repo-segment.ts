/** Parse /repos/{owner~repo} dynamic segment from the UI router. */

// GitHub owner/repo charset rules. "~" can't appear in GitHub names; validating the charset is
// defense-in-depth so a malformed segment never reaches the API.
const OWNER_PATTERN = /^(?!-)(?!.*--)[a-zA-Z0-9-]{1,39}(?<!-)$/
// "." and ".." match the charset but are path-traversal segments: encodeURIComponent leaves them alone
// and fetch would collapse /repos/acme/../x, so they are not repository names.
const REPO_PATTERN = /^(?!\.{1,2}$)[\w.-]+$/

export function parseOwnerRepo(segment: string): { owner: string; repo: string } | null {
  const parts = segment.split("~")
  if (parts.length !== 2) return null
  const [owner, repo] = parts.map((p) => p.trim())
  if (!owner || !repo) return null
  if (!OWNER_PATTERN.test(owner) || !REPO_PATTERN.test(repo)) return null
  return { owner, repo }
}
