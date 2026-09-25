import { afterEach, describe, expect, it, vi } from "vitest"

describe("githubWebUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  it("defaults to github.com", async () => {
    const { githubWebUrl } = await import("@/lib/github-web")
    expect(githubWebUrl("apps/clevis/installations/new")).toBe("https://github.com/apps/clevis/installations/new")
  })

  it("uses the configured GHES web origin", async () => {
    vi.stubEnv("NEXT_PUBLIC_GITHUB_WEB_BASE", "https://github.acme.dev/")
    const { githubWebUrl } = await import("@/lib/github-web")
    expect(githubWebUrl("/octocat")).toBe("https://github.acme.dev/octocat")
  })
})
