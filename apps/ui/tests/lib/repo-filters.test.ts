import { describe, expect, it } from "vitest";

import { NO_REPO_FILTERS, filterRepos, hasActiveRepoFilters, repoLanguages, type RepoFilters } from "@/lib/repo-filters";
import type { RepoSummary } from "@/lib/api/types";

const NOW = Date.parse("2026-10-01T00:00:00Z");

function repo(over: Partial<RepoSummary> & { name: string }): RepoSummary {
  return {
    full_name: `acme/${over.name}`,
    private: false,
    description: null,
    language: null,
    stargazers_count: 0,
    forks_count: 0,
    watchers_count: 0,
    open_issues_count: 0,
    pushed_at: "2026-09-25T00:00:00Z",
    default_branch: "main",
    html_url: `https://github.com/acme/${over.name}`,
    ...over,
  };
}

const REPOS = [
  repo({ name: "api", language: "Python", private: true }),
  repo({ name: "web", language: "TypeScript" }),
  repo({ name: "old-lib", language: "Python", archived: true, pushed_at: "2024-01-01T00:00:00Z" }),
  repo({ name: "empty", pushed_at: null }),
  repo({ name: "odd-date", pushed_at: "not a date" }),
];

const names = (repos: RepoSummary[]) => repos.map((r) => r.name);
const only = (over: Partial<RepoFilters>): RepoFilters => ({ ...NO_REPO_FILTERS, ...over });

describe("filterRepos", () => {
  it("returns everything when no filter is set", () => {
    expect(names(filterRepos(REPOS, NO_REPO_FILTERS, NOW))).toEqual(["api", "web", "old-lib", "empty", "odd-date"]);
  });

  it("filters by language", () => {
    expect(names(filterRepos(REPOS, only({ language: "Python" }), NOW))).toEqual(["api", "old-lib"]);
  });

  it("filters by visibility", () => {
    expect(names(filterRepos(REPOS, only({ visibility: "private" }), NOW))).toEqual(["api"]);
    expect(names(filterRepos(REPOS, only({ visibility: "public" }), NOW))).toEqual(["web", "old-lib", "empty", "odd-date"]);
  });

  it("filters by archived status, treating a missing flag as not archived", () => {
    expect(names(filterRepos(REPOS, only({ status: "archived" }), NOW))).toEqual(["old-lib"]);
    expect(names(filterRepos(REPOS, only({ status: "active" }), NOW))).toEqual(["api", "web", "empty", "odd-date"]);
  });

  it("treats a repo as stale after 90 days without a push or when it was never pushed, but not on an unparsable date", () => {
    expect(names(filterRepos(REPOS, only({ stale: true }), NOW))).toEqual(["old-lib", "empty"]);
  });

  it("is stale only beyond the boundary", () => {
    const exactly90 = repo({ name: "edge", pushed_at: new Date(NOW - 90 * 24 * 60 * 60 * 1000).toISOString() });
    const just_over = repo({ name: "over", pushed_at: new Date(NOW - 91 * 24 * 60 * 60 * 1000).toISOString() });
    expect(names(filterRepos([exactly90, just_over], only({ stale: true }), NOW))).toEqual(["over"]);
  });

  it("requires every active filter to match", () => {
    expect(names(filterRepos(REPOS, only({ language: "Python", status: "archived", stale: true }), NOW))).toEqual(["old-lib"]);
    expect(filterRepos(REPOS, only({ language: "TypeScript", visibility: "private" }), NOW)).toEqual([]);
  });
});

describe("repoLanguages", () => {
  it("lists each language once, alphabetised, skipping repos without one", () => {
    expect(repoLanguages(REPOS)).toEqual(["Python", "TypeScript"]);
  });
});

describe("hasActiveRepoFilters", () => {
  it("is false for the defaults and true when any filter is set", () => {
    expect(hasActiveRepoFilters(NO_REPO_FILTERS)).toBe(false);
    expect(hasActiveRepoFilters(only({ language: "Go" }))).toBe(true);
    expect(hasActiveRepoFilters(only({ visibility: "public" }))).toBe(true);
    expect(hasActiveRepoFilters(only({ status: "active" }))).toBe(true);
    expect(hasActiveRepoFilters(only({ stale: true }))).toBe(true);
  });
});
