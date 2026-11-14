import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const reposListMock = vi.fn();
const reposPullsMock = vi.fn();
const prNudgesSweepMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
    },
    repos: {
      list: (...args: unknown[]) => reposListMock(...args),
      pulls: (...args: unknown[]) => reposPullsMock(...args),
    },
    prNudges: {
      sweep: (...args: unknown[]) => prNudgesSweepMock(...args),
    },
  },
}));

const downloadTextFileMock = vi.fn();
vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));

import PullRequestsPage from "@/app/pulls/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PullRequestsPage />
    </QueryClientProvider>,
  );
}

function pull(overrides: Partial<{ number: number; title: string; user: string | null; created_at: string; updated_at: string | null; draft: boolean; html_url: string }> = {}) {
  return {
    number: 1,
    title: "Fix the thing",
    user: "octocat",
    created_at: "2026-07-01T00:00:00Z",
    html_url: "https://github.com/acme/demo/pull/1",
    ...overrides,
  };
}

describe("PullRequestsPage", () => {
  beforeEach(() => {
    localStorage.clear();
    tokensResolveMock.mockReset();
    reposListMock.mockReset();
    reposPullsMock.mockReset();
    prNudgesSweepMock.mockReset();
    downloadTextFileMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });
    reposPullsMock.mockResolvedValue({ repository: "acme/demo", total: 0, pulls: [] });
    prNudgesSweepMock.mockResolvedValue({ mode: "comment", stale_days: 3, results: [] });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a configure prompt when no org is set", async () => {
    renderPage();
    expect(await screen.findByText(/No account selected yet/)).toBeInTheDocument();
    expect(reposListMock).not.toHaveBeenCalled();
  });

  it("explains the page is org-only and skips org calls for a personal scope", async () => {
    localStorage.setItem("active_scope", JSON.stringify({ kind: "personal", login: "octocat" }));
    renderPage();
    expect(await screen.findByText(/lists organization repositories/)).toBeInTheDocument();
    expect(reposListMock).not.toHaveBeenCalled();
  });

  it("shows an empty state when the org has repos but no open pull requests", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [{ name: "demo", full_name: "acme/demo", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/demo" }],
    });

    renderPage();

    await waitFor(() => expect(reposPullsMock).toHaveBeenCalledWith("acme", "acme", "demo", ""));
    expect(await screen.findByText("No open pull requests")).toBeInTheDocument();
  });

  it("shows loading, not the empty state, while the repository list is still pending (#796)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockReturnValue(new Promise(() => {})); // never settles

    renderPage();

    // The data call goes out at once: nothing waits on a saved-token lookup any more.
    await waitFor(() => expect(reposListMock).toHaveBeenCalledWith("acme", ""));
    expect(tokensResolveMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/No open pull requests/)).not.toBeInTheDocument();
  });

  it("aggregates pull requests across every repo in the org, sorted newest first", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
        { name: "web", full_name: "acme/web", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/web" },
      ],
    });
    reposPullsMock.mockImplementation((_org: string, _owner: string, repo: string) => {
      if (repo === "api") {
        return Promise.resolve({
          repository: "acme/api",
          total: 1,
          pulls: [pull({ number: 10, title: "Older PR", created_at: "2026-06-01T00:00:00Z", html_url: "https://github.com/acme/api/pull/10" })],
        });
      }
      return Promise.resolve({
        repository: "acme/web",
        total: 1,
        pulls: [pull({ number: 20, title: "Newer PR", created_at: "2026-07-10T00:00:00Z", html_url: "https://github.com/acme/web/pull/20" })],
      });
    });

    renderPage();

    await waitFor(() => expect(screen.getByText(/2 total/)).toBeInTheDocument());
    const rows = screen.getAllByRole("row").slice(1).map((r) => r.textContent);
    // Newest first: "Newer PR" (07-10) before "Older PR" (06-01).
    expect(rows[0]).toContain("Newer PR");
    expect(rows[1]).toContain("Older PR");
  });

  it("still renders other repos' pull requests when one repo's fetch fails", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
        { name: "web", full_name: "acme/web", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/web" },
      ],
    });
    reposPullsMock.mockImplementation((_org: string, _owner: string, repo: string) => {
      if (repo === "api") return Promise.reject(new Error("GitHub API unreachable"));
      return Promise.resolve({ repository: "acme/web", total: 1, pulls: [pull({ title: "Still shows up" })] });
    });

    renderPage();

    expect(await screen.findByText(/Still shows up/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/1 repository could not be loaded \(api\)/);
  });

  it("does not claim there are no pull requests when the only repos that loaded are empty and another failed", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
        { name: "web", full_name: "acme/web", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/web" },
      ],
    });
    reposPullsMock.mockImplementation((_o: string, _w: string, repo: string) =>
      repo === "api" ? Promise.reject(new Error("boom")) : Promise.resolve({ repository: "acme/web", total: 0, pulls: [] }),
    );

    renderPage();

    expect(await screen.findByText(/in the repositories that loaded/)).toBeInTheDocument();
    expect(screen.queryByText("No open pull requests")).not.toBeInTheDocument();
  });

  it("truncates a long list of failed repos, and the banner's Retry refetches them", async () => {
    localStorage.setItem("default_org", "acme");
    const names = ["a", "b", "c", "d", "e", "f", "ok"];
    reposListMock.mockResolvedValue({
      org: "acme",
      total: names.length,
      repos: names.map((name) => ({ name, full_name: `acme/${name}`, private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: `https://github.com/acme/${name}` })),
    });
    reposPullsMock.mockImplementation((_o: string, _w: string, repo: string) =>
      repo === "ok" ? Promise.resolve({ repository: "acme/ok", total: 1, pulls: [pull({ title: "Survivor" })] }) : Promise.reject(new Error("boom")),
    );

    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/6 repositories could not be loaded \(a, b, c, d, e, …\)/);
    const callsBefore = reposPullsMock.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(reposPullsMock.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  it("shows an error, not 'No open pull requests', when every repo's fetch fails", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
        { name: "web", full_name: "acme/web", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/web" },
      ],
    });
    reposPullsMock.mockRejectedValue(new Error("Bad credentials"));

    renderPage();

    expect(await screen.findByText("Bad credentials")).toBeInTheDocument();
    expect(screen.queryByText("No open pull requests")).not.toBeInTheDocument();
    const callsBefore = reposPullsMock.mock.calls.length;
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 1, pulls: [pull({ title: "Recovered" })] });

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect((await screen.findAllByText(/Recovered/)).length).toBeGreaterThan(0);
    expect(reposPullsMock.mock.calls.length).toBeGreaterThan(callsBefore);
  });

  it("shows an error with retry when the repo list fails to load", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockRejectedValue(new Error("GitHub API unreachable"));

    renderPage();

    expect(await screen.findByText("GitHub API unreachable")).toBeInTheDocument();
    const retryButton = screen.getByRole("button", { name: /retry/i });
    expect(retryButton).toBeInTheDocument();

    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });
    fireEvent.click(retryButton);

    await waitFor(() => expect(screen.getByText("No open pull requests")).toBeInTheDocument());
  });

  it("shows a generic error message when the repo list rejects with a non-Error value", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockRejectedValue("string rejection, not an Error instance");

    renderPage();

    expect(await screen.findByText("Failed to load repositories.")).toBeInTheDocument();
  });

  it("regroups the open PRs by author when the 'by author' toggle is selected (issue #284)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [{ name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" }],
    });
    reposPullsMock.mockResolvedValue({
      repository: "acme/api",
      total: 2,
      pulls: [
        pull({ number: 1, title: "alice one", user: "alice", html_url: "https://github.com/acme/api/pull/1" }),
        pull({ number: 2, title: "bob one", user: "bob", html_url: "https://github.com/acme/api/pull/2" }),
      ],
    });

    renderPage();

    await waitFor(() => expect(screen.getByText(/2 total/)).toBeInTheDocument());
    // repo view: a real table
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);

    fireEvent.click(screen.getByRole("button", { name: "by author" }));

    await waitFor(() => expect(screen.queryByRole("table")).not.toBeInTheDocument());
    // Author-grouped card headings: the author login is its own text node ("alice",
    // "bob"), with the per-author count in a sibling span.
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText(/#1 alice one/)).toBeInTheDocument();
    expect(screen.getByText(/#2 bob one/)).toBeInTheDocument();
  });

  it("falls back to 'unknown' when a pull request has no author", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [{ name: "demo", full_name: "acme/demo", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/demo" }],
    });
    reposPullsMock.mockResolvedValue({ repository: "acme/demo", total: 1, pulls: [pull({ user: null })] });

    renderPage();

    expect(await screen.findByText("unknown")).toBeInTheDocument();
  });

  it("nudges stale PRs across every repo and reports the count (issue #289)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
        { name: "web", full_name: "acme/web", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/web" },
      ],
    });
    reposPullsMock.mockResolvedValue({
      repository: "acme/api",
      total: 1,
      pulls: [pull({ number: 7, title: "Waiting PR" })],
    });
    prNudgesSweepMock.mockImplementation((_org: string, _owner: string, repo: string, _token: string, dryRun?: boolean) =>
      Promise.resolve({
        mode: "comment",
        stale_days: 3,
        dry_run: !!dryRun,
        results: repo === "api" ? [{ number: 1, title: "Old PR", action: dryRun ? "would-comment" : "commented" }] : [],
      }),
    );

    renderPage();

    // Wait for the PR list to load so the button is enabled.
    await screen.findAllByText(/Waiting PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));

    // Step 1: a dry-run preview lists exactly what would be nudged, and posts nothing.
    expect(await screen.findByText("These 1 pull request would be nudged:")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "api #1 Old PR" })).toHaveAttribute("href", "https://github.com/acme/api/pull/1");
    expect(prNudgesSweepMock).toHaveBeenCalledWith("acme", "acme", "api", "", true);
    expect(prNudgesSweepMock).not.toHaveBeenCalledWith("acme", "acme", "api", "");

    // Step 2: confirming sends the real run and lists what was touched.
    fireEvent.click(screen.getByRole("button", { name: "Nudge 1 pull request" }));
    await waitFor(() => expect(screen.getByText("Nudged 1 pull request.")).toBeInTheDocument());
    // Only the previewed repository is called, and only for the previewed PR numbers.
    expect(prNudgesSweepMock).toHaveBeenCalledWith("acme", "acme", "api", "", false, [1]);
    expect(prNudgesSweepMock).not.toHaveBeenCalledWith("acme", "acme", "web", "", false, expect.anything());
    expect(screen.getByRole("link", { name: "api #1 Old PR" })).toBeInTheDocument();
  });

  it("says so when the preview finds nothing to nudge, without offering to send", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [{ name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" }],
    });
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 1, pulls: [pull({ number: 7, title: "Waiting PR" })] });
    prNudgesSweepMock.mockResolvedValue({ mode: "comment", stale_days: 3, dry_run: true, results: [{ number: 7, title: "x", action: "skipped-not-stale" }] });

    renderPage();
    await screen.findAllByText(/Waiting PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));

    expect(await screen.findByText("No pull requests need a nudge right now.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Nudge \d/ })).not.toBeInTheDocument();
  });

  const nudgeRepos = {
    org: "acme",
    total: 1,
    repos: [{ name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" }],
  };

  it("says nudging is turned off instead of reporting that nothing needs a nudge (issue #984)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue(nudgeRepos);
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 1, pulls: [pull({ number: 7, title: "Waiting PR" })] });
    prNudgesSweepMock.mockResolvedValue({ mode: "off", stale_days: 3, dry_run: true, results: [] });

    renderPage();
    await screen.findAllByText(/Waiting PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));

    expect(await screen.findByText(/Nudging is turned off for this instance/)).toBeInTheDocument();
    expect(screen.queryByText("No pull requests need a nudge right now.")).not.toBeInTheDocument();
  });

  it("notes when the per-sweep cap left stale PRs behind, and refreshes the pull request list after a nudge (issue #984)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue(nudgeRepos);
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 1, pulls: [pull({ number: 1, title: "Old PR" })] });
    prNudgesSweepMock.mockImplementation((_o: string, _w: string, _r: string, _t: string, dryRun?: boolean) =>
      Promise.resolve({
        mode: "comment",
        stale_days: 3,
        dry_run: dryRun,
        results: [
          { number: 1, title: "Old PR", action: dryRun ? "would-comment" : "commented" },
          { number: 2, title: "Later PR", action: "skipped-per-sweep-cap" },
        ],
      }),
    );

    renderPage();
    await screen.findAllByText(/Old PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
    expect(await screen.findByText(/Only the oldest stale pull requests in api are included/)).toBeInTheDocument();
    const loadsBefore = reposPullsMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Nudge 1 pull request" }));

    expect(await screen.findByText(/Only the oldest stale pull requests in api were included/)).toBeInTheDocument();
    await waitFor(() => expect(reposPullsMock.mock.calls.length).toBeGreaterThan(loadsBefore));
  });

  it("reports partial failures instead of hiding them behind the count (issue #289)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
        { name: "web", full_name: "acme/web", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/web" },
      ],
    });
    reposPullsMock.mockResolvedValue({
      repository: "acme/api",
      total: 1,
      pulls: [pull({ number: 7, title: "Waiting PR" })],
    });
    prNudgesSweepMock.mockImplementation((_org: string, _owner: string, repo: string, _token: string, dryRun?: boolean) =>
      repo === "api"
        ? Promise.resolve({ mode: "comment", stale_days: 3, results: [{ number: 1, title: "x", action: dryRun ? "would-comment" : "commented" }] })
        : Promise.reject(new Error("GitHub API unreachable")),
    );

    renderPage();

    await screen.findAllByText(/Waiting PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
    // The preview already names the repository it could not check, and why.
    expect(await screen.findByText(/web could not be checked: GitHub API unreachable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Nudge 1 pull request" }));

    // web could not be previewed, so the send never touches it (it could hold PRs nobody approved).
    await waitFor(() => expect(screen.getByText("Nudged 1 pull request.")).toBeInTheDocument());
    expect(prNudgesSweepMock).not.toHaveBeenCalledWith("acme", "acme", "web", "", false, expect.anything());
  });

  it("shows a permission hint when every repo's nudge call fails (issue #289)", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [{ name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" }],
    });
    reposPullsMock.mockResolvedValue({
      repository: "acme/api",
      total: 1,
      pulls: [pull({ number: 7, title: "Waiting PR" })],
    });
    prNudgesSweepMock.mockRejectedValue(
      new Error(
        "GitHub rejected the nudge (403). Clevis's GitHub App (or token) needs the 'Pull requests' permission at Read and write. See docs/self-hosting.md.",
      ),
    );

    renderPage();

    await screen.findAllByText(/Waiting PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));

    await waitFor(() =>
      expect(screen.getByText(/Pull requests: write/)).toBeInTheDocument(),
    );
  });

  it("drops an open nudge preview when the organization changes", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockImplementation((org: string) =>
      Promise.resolve({ org, total: 1, repos: [{ name: "api", full_name: `${org}/api`, private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: `https://github.com/${org}/api` }] }),
    );
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 1, pulls: [pull({ number: 7, title: "Waiting PR" })] });
    prNudgesSweepMock.mockResolvedValue({ mode: "comment", stale_days: 3, results: [{ number: 1, title: "Old PR", action: "would-comment" }] });

    renderPage();
    await screen.findAllByText(/Waiting PR/);
    fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
    expect(await screen.findByRole("region", { name: "Nudge details" })).toBeInTheDocument();

    localStorage.setItem("default_org", "other");
    window.dispatchEvent(new Event("storage"));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Nudge details" })).not.toBeInTheDocument());
  });

  describe("nudge flow edges", () => {
    const repo = { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" };
    async function openPreview() {
      localStorage.setItem("default_org", "acme");
      reposListMock.mockResolvedValue({ org: "acme", total: 1, repos: [repo] });
      reposPullsMock.mockResolvedValue({ repository: "acme/api", total: 1, pulls: [pull({ number: 7, title: "Waiting PR" })] });
      renderPage();
      await screen.findAllByText(/Waiting PR/);
    }
    const wouldComment = { mode: "comment", stale_days: 3, results: [{ number: 1, title: "Old PR", action: "would-comment" }] };

    it("lets the preview be cancelled without sending anything", async () => {
      await openPreview();
      prNudgesSweepMock.mockResolvedValue(wouldComment);
      fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

      expect(screen.queryByRole("region", { name: "Nudge details" })).not.toBeInTheDocument();
      expect(prNudgesSweepMock).toHaveBeenCalledTimes(1);
    });

    it("shows progress while checking and while sending", async () => {
      await openPreview();
      let release: (v: unknown) => void = () => {};
      prNudgesSweepMock.mockImplementationOnce(() => new Promise((r) => (release = r)));
      fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
      expect(await screen.findByRole("button", { name: "Checking…" })).toBeDisabled();
      release(wouldComment);

      prNudgesSweepMock.mockImplementationOnce(() => new Promise((r) => (release = r)));
      fireEvent.click(await screen.findByRole("button", { name: "Nudge 1 pull request" }));
      expect((await screen.findAllByRole("button", { name: "Nudging…" })).length).toBeGreaterThan(0);
      release({ ...wouldComment, results: [{ number: 1, title: "Old PR", action: "commented" }] });
      expect(await screen.findByText("Nudged 1 pull request.")).toBeInTheDocument();
    });

    it("reports a failed send, with the plain count when no permission error is involved", async () => {
      await openPreview();
      prNudgesSweepMock.mockResolvedValueOnce(wouldComment);
      fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
      const send = await screen.findByRole("button", { name: "Nudge 1 pull request" });

      prNudgesSweepMock.mockRejectedValue(new Error("GitHub API unreachable"));
      fireEvent.click(send);

      expect(await screen.findByText(/Couldn't nudge any repository \(1 failed\): .*GitHub API unreachable/)).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Nudge details" })).not.toBeInTheDocument();
    });
    it("keeps the PRs already nudged when the only repository then stops on a GitHub error", async () => {
      await openPreview();
      prNudgesSweepMock.mockResolvedValueOnce(wouldComment);
      fireEvent.click(screen.getByRole("button", { name: "Nudge stale PRs" }));
      const send = await screen.findByRole("button", { name: "Nudge 1 pull request" });

      prNudgesSweepMock.mockResolvedValue({
        ...wouldComment,
        results: [
          { number: 1, title: "Old PR", action: "commented" },
          { number: 2, title: "Next PR", action: "error" },
        ],
      });
      fireEvent.click(send);

      expect(await screen.findByText(/Nudged 1 pull request\. 1 repository failed/)).toBeInTheDocument();
      expect(screen.getByText(/stopped early/)).toBeInTheDocument();
    });
  });

  it("exports every open PR as CSV with author, date and link", async () => {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
      ],
    });
    reposPullsMock.mockResolvedValue({
      repository: "acme/api",
      total: 2,
      pulls: [
        pull({ number: 1, title: "=HYPERLINK(1)", user: "alice", html_url: "https://github.com/acme/api/pull/1" }),
        pull({ number: 2, title: "Second", user: null, html_url: "https://github.com/acme/api/pull/2" }),
      ],
    });

    renderPage();

    // each sortable header runs its sortValue
    fireEvent.click(await screen.findByRole("button", { name: /^Repo/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Pull Request/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Author/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Opened/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Export CSV/ }));
    const [filename, csv] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-pull-requests-acme-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(csv).toContain("Repo,Pull Request,Author,Opened,Updated,Status,Link");
    expect(csv).toContain("unknown");
    expect(csv).toContain("https://github.com/acme/api/pull/2");
    expect(csv).not.toMatch(/(^|,)"?=HYPERLINK/m);
  });

  // --- triage (#812) ---
  const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

  function mockTriageRepo(pulls: ReturnType<typeof pull>[], stale_days = 3) {
    localStorage.setItem("default_org", "acme");
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 1,
      repos: [
        { name: "api", full_name: "acme/api", private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: "https://github.com/acme/api" },
      ],
    });
    reposPullsMock.mockResolvedValue({ repository: "acme/api", total: pulls.length, pulls, stale_days });
  }

  const titlesInOrder = () =>
    screen.getAllByRole("link", { name: /^#\d+ / }).map((a) => a.textContent?.replace(/^#\d+ /, ""));

  it("sorts oldest first, newest first or by recent activity", async () => {
    mockTriageRepo([
      pull({ number: 1, title: "Mid", created_at: daysAgo(10), updated_at: daysAgo(1) }),
      pull({ number: 2, title: "Oldest", created_at: daysAgo(30), updated_at: daysAgo(20) }),
      pull({ number: 3, title: "Newest", created_at: daysAgo(2), updated_at: daysAgo(2) }),
    ]);
    renderPage();
    await screen.findByText(/Mid/);
    expect(titlesInOrder()).toEqual(["Newest", "Mid", "Oldest"]); // default: newest first, as before

    const sort = screen.getByLabelText("Sort pull requests");
    fireEvent.change(sort, { target: { value: "oldest" } });
    expect(titlesInOrder()).toEqual(["Oldest", "Mid", "Newest"]);

    fireEvent.change(sort, { target: { value: "updated" } });
    expect(titlesInOrder()).toEqual(["Mid", "Newest", "Oldest"]);
  });

  it("marks draft, stale and open PRs, using the instance's stale threshold", async () => {
    mockTriageRepo(
      [
        pull({ number: 1, title: "Quiet", created_at: daysAgo(10), updated_at: daysAgo(9) }),
        pull({ number: 2, title: "Draft one", created_at: daysAgo(30), updated_at: daysAgo(30), draft: true }),
        pull({ number: 3, title: "Active", created_at: daysAgo(30), updated_at: daysAgo(1) }),
        pull({ number: 4, title: "Young", created_at: daysAgo(2), updated_at: daysAgo(2) }),
      ],
      7,
    );
    renderPage();
    await screen.findByText(/Quiet/);

    const status = (title: string) => screen.getByText(new RegExp(title)).closest("tr")!.textContent;
    expect(status("Quiet")).toContain("Stale");
    expect(status("Draft one")).toContain("Draft"); // a draft is never stale
    expect(status("Active")).toContain("Open"); // old, but touched within the threshold
    expect(status("Young")).toContain("Open");
    expect(screen.getByText("Stale")).toHaveClass("text-warning");
  });

  it("is not stale under a longer threshold, and exports the status", async () => {
    mockTriageRepo([pull({ number: 1, title: "Quiet", created_at: daysAgo(10), updated_at: daysAgo(9) })], 30);
    renderPage();
    await screen.findByText(/Quiet/);
    expect(screen.queryByText("Stale")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    expect(downloadTextFileMock.mock.calls[0][1]).toContain(",Open,");
  });

  it("shows draft and age in the by-author view too", async () => {
    mockTriageRepo([pull({ number: 2, title: "Draft one", created_at: daysAgo(30), updated_at: daysAgo(30), draft: true })]);
    renderPage();
    await screen.findByText(/Draft one/);
    fireEvent.click(screen.getByRole("button", { name: "by author" }));
    expect(screen.getByText("Draft")).toBeInTheDocument();
    expect(screen.getByText(/month ago/)).toBeInTheDocument();
  });
});
