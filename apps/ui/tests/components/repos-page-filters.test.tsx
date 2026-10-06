import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const reposListMock = vi.fn();
const installationsListMock = vi.fn();
const installationsListForOrgMock = vi.fn();

vi.mock("next/navigation", () => ({ useParams: () => ({}) }));

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: { resolve: (...args: unknown[]) => tokensResolveMock(...args), upsert: vi.fn() },
    repos: {
      list: (...args: unknown[]) => reposListMock(...args),
      stats: vi.fn().mockResolvedValue({ commit_activity: [], latest_release: null }),
      pulls: vi.fn().mockResolvedValue({ total: 0, pulls: [] }),
    },
    installations: {
      list: (...args: unknown[]) => installationsListMock(...args),
      listForOrg: (...args: unknown[]) => installationsListForOrgMock(...args),
    },
  },
}));

import ReposPage from "@/app/repos/page";

function repo(name: string, over: Record<string, unknown> = {}) {
  return {
    name,
    full_name: `acme/${name}`,
    private: false,
    description: null,
    language: null,
    stargazers_count: 0,
    forks_count: 0,
    watchers_count: 0,
    open_issues_count: 0,
    pushed_at: new Date().toISOString(),
    default_branch: "main",
    html_url: `https://github.com/acme/${name}`,
    ...over,
  };
}

const LONG_AGO = "2020-01-01T00:00:00Z";
const REPOS = [
  repo("api", { language: "Python", private: true }),
  repo("web", { language: "TypeScript" }),
  repo("legacy", { language: "Python", archived: true, pushed_at: LONG_AGO }),
];

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReposPage />
    </QueryClientProvider>,
  );
}

async function loadReposByHand() {
  fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
  fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));
  await screen.findByText("api");
}

const visibleNames = () => ["api", "web", "legacy"].filter((n) => screen.queryByRole("link", { name: n }));

describe("ReposPage filters", () => {
  beforeEach(() => {
    localStorage.clear();
    tokensResolveMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    reposListMock.mockReset();
    reposListMock.mockResolvedValue({ org: "acme", total: 3, repos: REPOS });
    installationsListMock.mockReset();
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockReset();
    installationsListForOrgMock.mockResolvedValue([]);
  });

  afterEach(() => cleanup());

  it("offers only the languages present in the loaded org and filters by one", async () => {
    renderPage();
    await loadReposByHand();

    const language = screen.getByLabelText("Filter by language");
    expect(Array.from(language.querySelectorAll("option")).map((o) => o.textContent)).toEqual([
      "Any language",
      "Python",
      "TypeScript",
    ]);

    fireEvent.change(language, { target: { value: "TypeScript" } });
    await waitFor(() => expect(visibleNames()).toEqual(["web"]));
  });

  it("filters by visibility, status and recent activity, and clears them again", async () => {
    renderPage();
    await loadReposByHand();
    expect(screen.queryByRole("button", { name: "Clear filters" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Filter by visibility"), { target: { value: "private" } });
    await waitFor(() => expect(visibleNames()).toEqual(["api"]));

    fireEvent.change(screen.getByLabelText("Filter by visibility"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "archived" } });
    await waitFor(() => expect(visibleNames()).toEqual(["legacy"]));
    expect(screen.getByText("archived")).toBeInTheDocument(); // the row badge

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "all" } });
    fireEvent.click(screen.getByLabelText(/No push in 90\+ days/));
    await waitFor(() => expect(visibleNames()).toEqual(["legacy"]));

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(visibleNames().sort()).toEqual(["api", "legacy", "web"]));
  });

  it("explains an empty result caused by the filters and counts matches against the total", async () => {
    renderPage();
    await loadReposByHand();

    fireEvent.change(screen.getByLabelText("Filter by language"), { target: { value: "TypeScript" } });
    fireEvent.change(screen.getByLabelText("Filter by visibility"), { target: { value: "private" } });

    expect(await screen.findByText(/No repositories match your filters/)).toBeInTheDocument();
    expect(screen.getByText("0 of 3")).toBeInTheDocument();
  });

  it("falls back to any language when a different org's list no longer has the chosen one", async () => {
    renderPage();
    await loadReposByHand();
    fireEvent.change(screen.getByLabelText("Filter by language"), { target: { value: "TypeScript" } });
    await waitFor(() => expect(visibleNames()).toEqual(["web"]));

    reposListMock.mockResolvedValue({ org: "other", total: 1, repos: [repo("solo", { language: "Go" })] });
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "other" } });
    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));

    await screen.findByText("solo");
    expect(screen.getByLabelText("Filter by language")).toHaveValue("");
  });
});

describe("ReposPage auto-load", () => {
  beforeEach(() => {
    localStorage.clear();
    tokensResolveMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    reposListMock.mockReset();
    reposListMock.mockResolvedValue({ org: "acme", total: 3, repos: REPOS });
    installationsListMock.mockReset();
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockReset();
    installationsListForOrgMock.mockResolvedValue([]);
  });

  afterEach(() => cleanup());

  function setScope(login: string) {
    localStorage.setItem("active_scope", JSON.stringify({ kind: "org", login }));
  }

  it("loads the active org's repositories on its own when the GitHub App covers it", async () => {
    setScope("acme");
    installationsListForOrgMock.mockResolvedValue([
      { id: 2, account_login: "acme", account_type: "Organization", installation_id: 99, created_at: "2026-07-20T00:00:00Z" },
    ]);

    renderPage();

    expect(await screen.findByText("api")).toBeInTheDocument();
    expect(reposListMock).toHaveBeenCalledTimes(1);
    expect(reposListMock).toHaveBeenCalledWith("acme", "");
  });

  it("loads with the saved token when there is no installation", async () => {
    setScope("acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_saved" });

    renderPage();

    expect(await screen.findByText("api")).toBeInTheDocument();
    expect(reposListMock).toHaveBeenCalledWith("acme", "ghp_saved");
  });

  it("does not guess: with neither an installation nor a saved token the user still clicks Load", async () => {
    setScope("acme");

    renderPage();

    await waitFor(() => expect(tokensResolveMock).toHaveBeenCalled());
    await waitFor(() => expect(installationsListForOrgMock).toHaveBeenCalled());
    expect(reposListMock).not.toHaveBeenCalled();
  });

  it("never auto-loads for a name the user types, only for the active scope's org", async () => {
    installationsListForOrgMock.mockResolvedValue([
      { id: 2, account_login: "acme", account_type: "Organization", installation_id: 99, created_at: "2026-07-20T00:00:00Z" },
    ]);

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    await waitFor(() => expect(installationsListForOrgMock).toHaveBeenCalledWith("acme"));
    await waitFor(() => expect(screen.queryByText("GitHub Token")).not.toBeInTheDocument());
    expect(reposListMock).not.toHaveBeenCalled();
  });

  it("loads once per org: editing the box or clicking Load again doesn't re-trigger it", async () => {
    setScope("acme");
    installationsListForOrgMock.mockResolvedValue([
      { id: 2, account_login: "acme", account_type: "Organization", installation_id: 99, created_at: "2026-07-20T00:00:00Z" },
    ]);

    renderPage();
    await screen.findByText("api");
    expect(reposListMock).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByPlaceholderText("Filter by name…"), { target: { value: "w" } });
    await waitFor(() => expect(screen.queryByText("api")).not.toBeInTheDocument());
    expect(reposListMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /load repositories/i }));
    await waitFor(() => expect(reposListMock).toHaveBeenCalledTimes(2));
  });
});
