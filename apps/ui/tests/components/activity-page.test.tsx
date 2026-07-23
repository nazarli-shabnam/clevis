import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jobsListMock = vi.fn();
const tokensResolveMock = vi.fn();
const githubEventsMock = vi.fn();
const githubFailedRunsMock = vi.fn();
const githubReleaseTimelineMock = vi.fn();
const analyticsCockpitMock = vi.fn();
const reposListMock = vi.fn();
const reposPullsMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    jobs: {
      list: (...args: unknown[]) => jobsListMock(...args),
    },
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
    },
    github: {
      events: (...args: unknown[]) => githubEventsMock(...args),
      failedRuns: (...args: unknown[]) => githubFailedRunsMock(...args),
      releaseTimeline: (...args: unknown[]) => githubReleaseTimelineMock(...args),
    },
    analytics: {
      cockpit: (...args: unknown[]) => analyticsCockpitMock(...args),
    },
    repos: {
      list: (...args: unknown[]) => reposListMock(...args),
      pulls: (...args: unknown[]) => reposPullsMock(...args),
    },
  },
}));

import ActivityPage from "@/app/activity/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActivityPage />
    </QueryClientProvider>,
  );
}

describe("ActivityPage", () => {
  beforeEach(() => {
    localStorage.clear();
    jobsListMock.mockReset();
    tokensResolveMock.mockReset();
    githubEventsMock.mockReset();
    githubFailedRunsMock.mockReset();
    githubReleaseTimelineMock.mockReset();
    analyticsCockpitMock.mockReset();
    reposListMock.mockReset();
    reposPullsMock.mockReset();
    jobsListMock.mockResolvedValue([]);
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    githubFailedRunsMock.mockResolvedValue({ org: "acme", runs: [] });
    githubReleaseTimelineMock.mockResolvedValue({ org: "acme", releases: [] });
    analyticsCockpitMock.mockResolvedValue({
      repo_count: 0, member_count: 0, latest_score: null, score_trend: [], recent_events: [],
      open_pr_count: 0, pr_merge_rate_4w: [], commit_activity_4w: [], total_cache_size_bytes: 0,
      cache_job_success_rate: 0, commit_heatmap_52w: [],
    });
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 0, pulls: [] });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a configure prompt when no org is set", async () => {
    renderPage();

    expect(await screen.findByText(/No organization configured yet/)).toBeInTheDocument();
  });

  it("renders the event feed and the jobs side panel when an org is configured", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    githubEventsMock.mockResolvedValue({
      org: "acme",
      events: [
        {
          id: "1",
          type: "PushEvent",
          actor: "alice",
          actor_avatar: "https://avatars/alice.png",
          repo: "acme/api",
          summary: "pushed 3 commits to main",
          created_at: new Date().toISOString(),
        },
      ],
    });
    jobsListMock.mockResolvedValue([
      { id: 1, job_type: "clear_actions_cache", status: "done", result: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
    ]);

    renderPage();

    expect(await screen.findByText(/pushed 3 commits to main/)).toBeInTheDocument();
    expect(await screen.findByText("#1")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/refreshes in \d+s/)).toBeInTheDocument());
  });

  it("resolves a short (1-2 character) org login instead of getting stuck unconfigured", async () => {
    localStorage.setItem("default_org", "hp");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    githubEventsMock.mockResolvedValue({ org: "hp", events: [] });

    renderPage();

    await waitFor(() => expect(tokensResolveMock).toHaveBeenCalledWith("hp"));
    expect(await screen.findByText(/no events yet/)).toBeInTheDocument();
  });

  it("shows an error instead of crashing when the events query runs without a resolved token", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "" });

    renderPage();

    // configured is false when token is empty, so the feed never queries and the
    // configure prompt is shown -- no uncaught exception from a bare `.token` read.
    expect(await screen.findByText(/No organization configured yet/)).toBeInTheDocument();
    expect(githubEventsMock).not.toHaveBeenCalled();
  });

  it("renders the CI failure log and release timeline", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    githubEventsMock.mockResolvedValue({ org: "acme", events: [] });
    githubFailedRunsMock.mockResolvedValue({
      org: "acme",
      runs: [
        {
          repo: "acme/api", workflow_name: "CI", branch: "main", run_id: 1,
          started_at: new Date().toISOString(), duration_seconds: 60,
          url: "https://github.com/acme/api/actions/runs/1", actor: "alice", consecutive_failures: 3,
        },
      ],
    });
    githubReleaseTimelineMock.mockResolvedValue({
      org: "acme",
      releases: [
        {
          repo: "acme/api", tag_name: "v1.0.0", name: "v1.0.0", published_at: new Date().toISOString(),
          is_prerelease: false, body_preview: "Initial release", url: "https://github.com/acme/api/releases/v1.0.0",
        },
      ],
    });

    renderPage();

    await waitFor(() => expect(githubFailedRunsMock).toHaveBeenCalledWith("acme", "ghp_test"));
    expect(await screen.findByText(/acme\/api · CI/)).toBeInTheDocument();
    expect(screen.getByText("×3")).toBeInTheDocument();
    expect(await screen.findByText(/acme\/api · v1.0.0/)).toBeInTheDocument();
  });

  it("renders the commit heatmap from the cockpit endpoint", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    githubEventsMock.mockResolvedValue({ org: "acme", events: [] });
    analyticsCockpitMock.mockResolvedValue({
      repo_count: 1, member_count: 1, latest_score: null, score_trend: [], recent_events: [],
      open_pr_count: 0, pr_merge_rate_4w: [], commit_activity_4w: [], total_cache_size_bytes: 0,
      cache_job_success_rate: 0, commit_heatmap_52w: Array.from({ length: 52 }, (_, i) => (i === 51 ? 5 : 0)),
    });

    renderPage();

    await waitFor(() => expect(analyticsCockpitMock).toHaveBeenCalledWith("acme", "ghp_test"));
    expect(await screen.findByText("Commit Heatmap (52w)")).toBeInTheDocument();
    expect(screen.queryByText("No commit activity in the last year")).not.toBeInTheDocument();
  });

  it("groups open PRs by author under the PR Board tab", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    githubEventsMock.mockResolvedValue({ org: "acme", events: [] });
    reposListMock.mockResolvedValue({ org: "acme", total: 1, repos: [{ name: "api" }] });
    reposPullsMock.mockResolvedValue({
      repository: "acme/api",
      total: 1,
      pulls: [{ number: 5, title: "Fix bug", user: "alice", created_at: new Date().toISOString(), html_url: "https://github.com/acme/api/pull/5" }],
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "PR Board" }));

    await waitFor(() => expect(reposPullsMock).toHaveBeenCalledWith("acme", "acme", "api", "ghp_test"));
    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getByText("#5 Fix bug")).toBeInTheDocument();
  });
});
