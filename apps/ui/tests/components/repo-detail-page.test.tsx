import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const tokensUpsertMock = vi.fn();
const cacheListMock = vi.fn();
const cacheClearMock = vi.fn();
const reposStatsMock = vi.fn();
const reposPullsMock = vi.fn();
const analyticsOverviewMock = vi.fn();

let currentRepoParam = "acme~demo";

vi.mock("next/navigation", () => ({
  useParams: () => ({ repo: currentRepoParam }),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: (...args: unknown[]) => tokensUpsertMock(...args),
    },
    cache: {
      list: (...args: unknown[]) => cacheListMock(...args),
      clear: (...args: unknown[]) => cacheClearMock(...args),
    },
    repos: {
      stats: (...args: unknown[]) => reposStatsMock(...args),
      pulls: (...args: unknown[]) => reposPullsMock(...args),
    },
    analytics: {
      overview: (...args: unknown[]) => analyticsOverviewMock(...args),
    },
  },
}));

import RepoDetailPage from "@/app/repos/[repo]/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <RepoDetailPage />
    </QueryClientProvider>,
  );
  return {
    ...utils,
    rerenderSamePage: () =>
      utils.rerender(
        <QueryClientProvider client={queryClient}>
          <RepoDetailPage />
        </QueryClientProvider>,
      ),
  };
}

describe("RepoDetailPage", () => {
  beforeEach(() => {
    currentRepoParam = "acme~demo";
    tokensResolveMock.mockReset();
    tokensUpsertMock.mockReset();
    cacheListMock.mockReset();
    cacheClearMock.mockReset();
    reposStatsMock.mockReset();
    reposPullsMock.mockReset();
    analyticsOverviewMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    reposStatsMock.mockResolvedValue({
      repository: "acme/demo",
      commit_activity: [],
      participation: {},
      contributors: [],
    });
    reposPullsMock.mockResolvedValue({ repository: "acme/demo", total: 0, pulls: [] });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows an error for an invalid repo route", () => {
    currentRepoParam = "not-a-valid-segment";
    renderPage();
    expect(screen.getByText(/invalid repository route/i)).toBeInTheDocument();
  });

  it("renders the open-PR count in the header and defaults to the Overview tab", async () => {
    reposPullsMock.mockResolvedValue({ repository: "acme/demo", total: 4, pulls: [] });

    renderPage();

    expect(screen.getByRole("tab", { name: /overview/i })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(screen.getByText(/4 open prs/i)).toBeInTheDocument());
  });

  it("shows the no-activity placeholder when commit_activity is empty", async () => {
    renderPage();
    await waitFor(() => expect(reposStatsMock).toHaveBeenCalledWith("acme", "acme", "demo", ""));
    expect(await screen.findByText(/no commit activity available yet/i)).toBeInTheDocument();
  });

  it("renders the commit-activity chart once stats resolve with data", async () => {
    reposStatsMock.mockResolvedValue({
      repository: "acme/demo",
      commit_activity: Array.from({ length: 4 }, (_, i) => ({ week: 1700000000 + i * 604800, total: i + 1, days: [] })),
      participation: {},
      contributors: [{ login: "octocat", total: 10 }, { login: "hubot", total: 3 }],
    });

    renderPage();

    await waitFor(() => expect(screen.queryByText(/no commit activity available yet/i)).not.toBeInTheDocument());
    expect(screen.getByText(/top contributors/i)).toBeInTheDocument();
  });

  it("does not fetch the org-wide security scan until the Security tab is opened", async () => {
    renderPage();

    await waitFor(() => expect(reposStatsMock).toHaveBeenCalled());
    expect(analyticsOverviewMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: /security/i }));

    await waitFor(() => expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", ""));
  });

  it("renders the security score once the scan resolves", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 82,
      total_checks: 10,
      failed_checks: 2,
      repo_count: 5,
      checks: [],
    });

    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /security/i }));

    await waitFor(() => expect(screen.getByText("82")).toBeInTheDocument());
    expect(screen.getByText(/8 passed/i)).toBeInTheDocument();
    expect(screen.getByText(/2 failed/i)).toBeInTheDocument();
  });

  it("switches to the Actions Cache tab and renders the shared cache panel", async () => {
    cacheListMock.mockResolvedValue({ repository: "acme/demo", total: 0, actions_caches: [] });

    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /actions cache/i }));

    expect(screen.getByRole("button", { name: /load caches/i })).toBeInTheDocument();
  });

  it("shows the loading placeholder then the resolved count in the header", async () => {
    let resolvePulls: (v: unknown) => void = () => {};
    reposPullsMock.mockReturnValue(new Promise((resolve) => { resolvePulls = resolve; }));

    renderPage();

    expect(await screen.findByText(/… open prs/i)).toBeInTheDocument();

    resolvePulls({ repository: "acme/demo", total: 7, pulls: [] });
    await waitFor(() => expect(screen.getByText(/7 open prs/i)).toBeInTheDocument());
  });

  it("uses an auto-resolved saved token for the stats/pulls/security calls", async () => {
    tokensResolveMock.mockReset();
    tokensResolveMock.mockResolvedValue({ token: "ghp_resolved_1234567890123456789" });

    renderPage();

    await waitFor(() =>
      expect(reposStatsMock).toHaveBeenCalledWith("acme", "acme", "demo", "ghp_resolved_1234567890123456789"),
    );
  });

  it("shows an inline error when the stats request fails", async () => {
    reposStatsMock.mockRejectedValue(new Error("GitHub API unreachable"));

    renderPage();

    expect(await screen.findByText(/github api unreachable/i)).toBeInTheDocument();
  });

  it("shows an inline error when the security scan fails", async () => {
    analyticsOverviewMock.mockRejectedValue(new Error("No GitHub App installation found"));

    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /security/i }));

    expect(await screen.findByText(/no github app installation found/i)).toBeInTheDocument();
  });

  it("remembers this org as the default before following the full-report link", async () => {
    localStorage.clear();
    renderPage();
    fireEvent.click(screen.getByRole("tab", { name: /security/i }));

    fireEvent.click(screen.getByRole("link", { name: /full report/i }));

    expect(localStorage.getItem("default_org")).toBe("acme");
  });

  it("wires each tab to its panel via aria-controls/id/aria-labelledby", () => {
    renderPage();

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    const overviewPanel = screen.getByRole("tabpanel");

    expect(overviewTab).toHaveAttribute("aria-controls", overviewPanel.id);
    expect(overviewPanel).toHaveAttribute("aria-labelledby", overviewTab.id);
  });

  it("only the active tab is in the normal tab order (roving tabindex)", () => {
    renderPage();

    expect(screen.getByRole("tab", { name: /overview/i })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: /actions cache/i })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: /security/i })).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus and selection with ArrowRight/ArrowLeft/Home/End", async () => {
    renderPage();

    const overviewTab = screen.getByRole("tab", { name: /overview/i });
    const cacheTab = screen.getByRole("tab", { name: /actions cache/i });
    const securityTab = screen.getByRole("tab", { name: /security/i });

    fireEvent.keyDown(overviewTab, { key: "ArrowRight" });
    expect(cacheTab).toHaveAttribute("aria-selected", "true");
    expect(cacheTab).toHaveFocus();

    fireEvent.keyDown(cacheTab, { key: "ArrowRight" });
    expect(securityTab).toHaveAttribute("aria-selected", "true");

    // Wraps past the last tab back to the first.
    fireEvent.keyDown(securityTab, { key: "ArrowRight" });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");

    // Wraps backward past the first tab to the last.
    fireEvent.keyDown(overviewTab, { key: "ArrowLeft" });
    expect(securityTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(securityTab, { key: "Home" });
    expect(overviewTab).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(overviewTab, { key: "End" });
    expect(securityTab).toHaveAttribute("aria-selected", "true");
  });

  it("resets to the Overview tab when navigating to a different repo", async () => {
    const { rerenderSamePage } = renderPage();

    fireEvent.click(screen.getByRole("tab", { name: /actions cache/i }));
    expect(screen.getByRole("tab", { name: /actions cache/i })).toHaveAttribute("aria-selected", "true");

    currentRepoParam = "acme~other";
    rerenderSamePage();

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /overview/i })).toHaveAttribute("aria-selected", "true"),
    );
  });
});
