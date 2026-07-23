/** Parse /repos/{owner~repo} dynamic segment from the UI router. */

// GitHub username rules: alphanumeric and single hyphens, no leading/trailing/consecutive
// hyphens, max 39 characters. Repo name rules: alphanumeric, hyphen, underscore, period.
// Splitting on "~" alone is currently safe (GitHub names can't contain "~"), but validating
// the charset here is defense-in-depth so a malformed segment never reaches api.repos.stats(...)
// relying entirely on the backend to reject it.
const OWNER_PATTERN = /^(?!-)(?!.*--)[a-zA-Z0-9-]{1,39}(?<!-)$/
const REPO_PATTERN = /^[\w.-]+$/

export function parseOwnerRepo(segment: string): { owner: string; repo: string } | null {
  const parts = segment.split("~")
  if (parts.length !== 2) return null
  const [owner, repo] = parts.map((p) => p.trim())
  if (!owner || !repo) return null
  if (!OWNER_PATTERN.test(owner) || !REPO_PATTERN.test(repo)) return null
  return { owner, repo }
}
