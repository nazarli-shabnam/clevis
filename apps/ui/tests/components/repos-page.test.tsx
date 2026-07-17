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
});
