/** Parse /repos/{owner~repo} dynamic segment from the UI router. */

export function parseOwnerRepo(segment: string): { owner: string; repo: string } | null {
  const parts = segment.split("~")
  if (parts.length !== 2) return null
  const [owner, repo] = parts
  if (!owner.trim() || !repo.trim()) return null
  return { owner: owner.trim(), repo: repo.trim() }
}
