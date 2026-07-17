import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const tokensUpsertMock = vi.fn();
const reposListMock = vi.fn();
const reposStatsMock = vi.fn();
const reposPullsMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: (...args: unknown[]) => tokensUpsertMock(...args),
    },
    repos: {
      list: (...args: unknown[]) => reposListMock(...args),
      stats: (...args: unknown[]) => reposStatsMock(...args),
      pulls: (...args: unknown[]) => reposPullsMock(...args),
    },
  },
}));

import ReposPage from "@/app/repos/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReposPage />
    </QueryClientProvider>,
  );
}

describe("ReposPage", () => {
  beforeEach(() => {
    localStorage.clear();
    tokensResolveMock.mockReset();
    tokensUpsertMock.mockReset();
    reposListMock.mockReset();
    reposStatsMock.mockReset();
    reposPullsMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    reposStatsMock.mockResolvedValue({ repository: "acme/demo", commit_activity: [], participation: {}, contributors: [] });
    reposPullsMock.mockResolvedValue({ repository: "acme/demo", total: 0, pulls: [] });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("keeps Load repositories disabled until an org is entered", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /load repositories/i })).toBeDisabled();
  });

  it("loads and renders repos for the entered org", async () => {
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [
        {
          name: "demo",
          full_name: "acme/demo",
          private: false,
          language: "Python",
          stargazers_count: 3,
          open_issues_count: 1,
          pushed_at: "2026-07-01T00:00:00Z",
          default_branch: "main",
          html_url: "https://github.com/acme/demo",
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const loadButton = screen.getByRole("button", { name: /load repositories/i });
    await waitFor(() => expect(loadButton).not.toBeDisabled());
    fireEvent.click(loadButton);

    await waitFor(() => expect(screen.getByText("demo")).toBeInTheDocument());
    expect(reposListMock).toHaveBeenCalledWith("acme", "");

    // Per-visible-row lazy fetches fire once the list is loaded.
    await waitFor(() => expect(reposStatsMock).toHaveBeenCalledWith("acme", "acme", "demo", ""));
    await waitFor(() => expect(reposPullsMock).toHaveBeenCalledWith("acme", "acme", "demo", ""));
  });

  it("shows an empty state when no repos match the name filter", async () => {
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [
        {
          name: "demo",
          full_name: "acme/demo",
          private: false,
          language: "Python",
          stargazers_count: 3,
          open_issues_count: 1,
          pushed_at: "2026-07-01T00:00:00Z",
          default_branch: "main",
          html_url: "https://github.com/acme/demo",
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));
    await waitFor(() => expect(screen.getByText("demo")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Filter by name…"), { target: { value: "nonexistent" } });

    await waitFor(() => expect(screen.getByText(/no repositories match your filter/i)).toBeInTheDocument());
  });

  it("shows an error message when loading repos fails", async () => {
    reposListMock.mockRejectedValue(new Error("No GitHub App installation found"));

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));

    await waitFor(() =>
      expect(screen.getByText(/no github app installation found/i)).toBeInTheDocument(),
    );
  });

  it("seeds the org field from the saved default_org", () => {
    localStorage.setItem("default_org", "acme");
    renderPage();
    expect(screen.getByPlaceholderText("e.g. octocat")).toHaveValue("acme");
  });

  it("shows the loading skeleton while the list request is in flight", async () => {
    let resolveList: (v: unknown) => void = () => {};
    reposListMock.mockReturnValue(new Promise((resolve) => { resolveList = resolve; }));

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));

    expect(await screen.findByText(/loading…/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /loading…/i })).toBeDisabled();

    resolveList({ org: "acme", total: 0, repos: [] });
    await waitFor(() => expect(screen.queryByText(/loading…/i)).not.toBeInTheDocument());
  });

  it("triggers a load on Enter in the organization field", async () => {
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });

    renderPage();

    const orgInput = screen.getByPlaceholderText("e.g. octocat");
    fireEvent.change(orgInput, { target: { value: "acme" } });
    fireEvent.keyDown(orgInput, { key: "Enter" });

    await waitFor(() => expect(reposListMock).toHaveBeenCalledWith("acme", ""));
  });

  it("shows a plain empty state (no filter hint) when the org has zero repos", async () => {
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));

    await waitFor(() => expect(screen.getByText("— no repositories match")).toBeInTheDocument());
  });

  it("auto-applies a resolved saved token and hides the save-token button", async () => {
    tokensResolveMock.mockReset();
    tokensResolveMock.mockResolvedValue({ token: "ghp_saved_token_1234567890123456" });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    await waitFor(() => expect(screen.getByPlaceholderText(/leave blank/i)).toHaveValue("ghp_saved_token_1234567890123456"));
    expect(screen.getByText(/saved/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save token for this org/i })).not.toBeInTheDocument();
  });

  it("offers to save a manually-entered token and calls upsert on click", async () => {
    tokensUpsertMock.mockResolvedValue({ org: "acme", label: null, created_at: "", updated_at: "" });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => expect(tokensResolveMock).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText(/leave blank/i), {
      target: { value: "ghp_manual_token_1234567890123456" },
    });

    const saveButton = await screen.findByRole("button", { name: /save token for this org/i });
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(tokensUpsertMock).toHaveBeenCalledWith("acme", "ghp_manual_token_1234567890123456"),
    );
  });

  it("renders the private-repo lock indicator, star count, and resolved PR count", async () => {
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [
        {
          name: "secret-repo",
          full_name: "acme/secret-repo",
          private: true,
          language: null,
          stargazers_count: 42,
          open_issues_count: 5,
          pushed_at: null,
          default_branch: "main",
          html_url: "https://github.com/acme/secret-repo",
        },
      ],
    });
    reposPullsMock.mockResolvedValue({ repository: "acme/secret-repo", total: 3, pulls: [] });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));

    await waitFor(() => expect(screen.getByText("secret-repo")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("3")).toBeInTheDocument());
    expect(screen.getByText("42")).toBeInTheDocument();
    // No language and no pushed_at both fall back to the em-dash placeholder.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("renders a sparkline instead of the no-activity message when a repo has recent commits", async () => {
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [
        {
          name: "demo",
          full_name: "acme/demo",
          private: false,
          language: "Python",
          stargazers_count: 3,
          open_issues_count: 1,
          pushed_at: "2026-07-01T00:00:00Z",
          default_branch: "main",
          html_url: "https://github.com/acme/demo",
        },
      ],
    });
    reposStatsMock.mockResolvedValue({
      repository: "acme/demo",
      commit_activity: Array.from({ length: 8 }, (_, i) => ({ week: i, total: i + 1, days: [] })),
      participation: {},
      contributors: [],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));

    await waitFor(() => expect(screen.getByText("demo")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText(/no recent activity/i)).not.toBeInTheDocument());
  });

  it("triggers a load on Enter in the token field", async () => {
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const tokenInput = screen.getByPlaceholderText(/leave blank/i);
    fireEvent.keyDown(tokenInput, { key: "Enter" });

    await waitFor(() => expect(reposListMock).toHaveBeenCalledWith("acme", ""));
  });
});
