import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jobsListMock = vi.fn();
const tokensResolveMock = vi.fn();
const analyticsOverviewMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    jobs: {
      list: (...args: unknown[]) => jobsListMock(...args),
    },
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
    },
    analytics: {
      overview: (...args: unknown[]) => analyticsOverviewMock(...args),
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

describe("OverviewPage stat cards", () => {
  beforeEach(() => {
    jobsListMock.mockReset();
    tokensResolveMock.mockReset();
    analyticsOverviewMock.mockReset();
    jobsListMock.mockResolvedValue([]);
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows Configure links for Repositories/Security Score when no org is configured", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText("Configure →")).toHaveLength(2);
    });

    expect(tokensResolveMock).not.toHaveBeenCalled();
    expect(analyticsOverviewMock).not.toHaveBeenCalled();

    const configureLinks = screen.getAllByRole("link", { name: /Configure →/i });
    for (const link of configureLinks) {
      expect(link).toHaveAttribute("href", "/security");
    }
  });

  it("shows a loading state once configured, then the real numbers", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });

    const gate = new Promise<{ owner: string; score: number; total_checks: number; failed_checks: number; repo_count: number; checks: [] }>(
      (resolve) => {
        setTimeout(() =>
          resolve({ owner: "acme", score: 87, total_checks: 3, failed_checks: 1, repo_count: 12, checks: [] }), 10);
      },
    );
    analyticsOverviewMock.mockReturnValue(gate);

    renderPage();

    await waitFor(() => {
      expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", "ghp_test");
    });

    await waitFor(() => {
      expect(screen.getByText("12")).toBeInTheDocument();
      expect(screen.getByText("87")).toBeInTheDocument();
    });

    expect(screen.queryAllByText("Configure →")).toHaveLength(0);
  });

  it("always renders Open PRs and Team Members as N/A, regardless of org state", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 1, failed_checks: 0, repo_count: 1, checks: [],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText("N/A")).toHaveLength(2);
    });
  });
});
