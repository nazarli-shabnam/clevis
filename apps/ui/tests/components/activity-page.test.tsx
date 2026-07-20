import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jobsListMock = vi.fn();
const tokensResolveMock = vi.fn();
const githubEventsMock = vi.fn();

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
    jobsListMock.mockResolvedValue([]);
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
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
});
