// GitHub's web origin, for links the browser opens (App install pages, profiles). Differs
// from the API base on GitHub Enterprise Server; baked at build time like other NEXT_PUBLIC_*.
const GITHUB_WEB_BASE = (process.env.NEXT_PUBLIC_GITHUB_WEB_BASE || "https://github.com").replace(/\/+$/, "")

export function githubWebUrl(path: string): string {
  return `${GITHUB_WEB_BASE}/${path.replace(/^\/+/, "")}`
}
