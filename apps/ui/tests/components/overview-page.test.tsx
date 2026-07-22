import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const cockpitMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
    },
    analytics: {
      cockpit: (...args: unknown[]) => cockpitMock(...args),
    },
  },
}));

import OverviewPage from "@/app/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OverviewPage />
    </QueryClientProvider>,
  );
}

const EMPTY_COCKPIT = {
  repo_count: 0,
  member_count: 0,
  latest_score: null,
  score_trend: [],
  recent_events: [],
  open_pr_count: 0,
  pr_merge_rate_4w: [],
  commit_activity_4w: [],
  total_cache_size_bytes: 0,
  cache_job_success_rate: 0,
};

describe("OverviewPage cockpit", () => {
  beforeEach(() => {
    tokensResolveMock.mockReset();
    cockpitMock.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows Configure links for all 4 stat cards when no org is configured", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText("Configure →")).toHaveLength(4);
    });

    expect(tokensResolveMock).not.toHaveBeenCalled();
    expect(cockpitMock).not.toHaveBeenCalled();

    const configureLinks = screen.getAllByRole("link", { name: /Configure →/i });
    for (const link of configureLinks) {
      expect(link).toHaveAttribute("href", "/security");
    }
  });

  it("fires a single cockpit call (no waterfall) and renders real values for all 4 cards", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    cockpitMock.mockResolvedValue({
      ...EMPTY_COCKPIT,
      repo_count: 12,
      open_pr_count: 5,
      latest_score: 87,
      score_trend: [70, 80, 87],
      member_count: 9,
    });

    renderPage();

    await waitFor(() => {
      expect(cockpitMock).toHaveBeenCalledWith("acme", "ghp_test");
    });

    await waitFor(() => {
      expect(screen.getByText("12")).toBeInTheDocument();
      expect(screen.getByText("5")).toBeInTheDocument();
      expect(screen.getByText("87")).toBeInTheDocument();
      expect(screen.getByText("9")).toBeInTheDocument();
    });

    expect(screen.queryAllByText("Configure →")).toHaveLength(0);
    // A single cockpit call once the token-resolve fetch settles -- not a second
    // waterfalled fetch for jobs/overview like the pre-cockpit page used to do.
    expect(cockpitMock).toHaveBeenCalledTimes(1);
  });

  it("renders empty states for charts and activity when the org has no data yet", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    cockpitMock.mockResolvedValue(EMPTY_COCKPIT);

    renderPage();

    await waitFor(() => {
      expect(cockpitMock).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByText("— no recent activity")).toBeInTheDocument();
    });
    expect(screen.getByText("No commit activity yet")).toBeInTheDocument();
    expect(screen.getByText("Run more scans to see a trend")).toBeInTheDocument();
    expect(screen.getByText("No pull request activity yet")).toBeInTheDocument();
  });

  it("renders recent events in the activity panel", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    cockpitMock.mockResolvedValue({
      ...EMPTY_COCKPIT,
      recent_events: [
        {
          id: "1",
          type: "PushEvent",
          actor: "alice",
          actor_avatar: "",
          repo: "acme/api",
          summary: "pushed 3 commits to main",
          created_at: "2026-07-18T00:00:00Z",
        },
      ],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("alice")).toBeInTheDocument();
    });
    expect(screen.getByText("pushed 3 commits to main")).toBeInTheDocument();
  });
});
