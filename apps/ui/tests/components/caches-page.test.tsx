import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const reposListMock = vi.fn();
const cacheListMock = vi.fn();
const cacheClearMock = vi.fn();
const jobsGetMock = vi.fn();
const orgsMineMock = vi.fn();
const toastInfo = vi.fn();
const toastSuccess = vi.fn();
const toastError = vi.fn();

vi.mock("@/components/ui/toast", () => ({
  toast: {
    info: (...a: unknown[]) => toastInfo(...a),
    success: (...a: unknown[]) => toastSuccess(...a),
    error: (...a: unknown[]) => toastError(...a),
  },
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: { resolve: (...a: unknown[]) => tokensResolveMock(...a) },
    repos: { list: (...a: unknown[]) => reposListMock(...a) },
    cache: {
      list: (...a: unknown[]) => cacheListMock(...a),
      clear: (...a: unknown[]) => cacheClearMock(...a),
    },
    jobs: { get: (...a: unknown[]) => jobsGetMock(...a) },
    orgs: { mine: (...a: unknown[]) => orgsMineMock(...a) },
  },
}));

import CachesPage from "@/app/caches/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CachesPage />
    </QueryClientProvider>,
  );
}

const repo = (name: string) => ({ name, full_name: `acme/${name}`, private: false, description: null, language: null, stargazers_count: 0, forks_count: 0, watchers_count: 0, open_issues_count: 0, pushed_at: null, default_branch: "main", html_url: `https://github.com/acme/${name}` });
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();
const entry = (size: number, accessedDaysAgo: number) => ({ id: 1, ref: "refs/heads/main", key: "k", version: "v", size_in_bytes: size, created_at: daysAgo(90), last_accessed_at: daysAgo(accessedDaysAgo) });
const MB = 1_048_576;

function seed(caches: Record<string, ReturnType<typeof entry>[]>) {
  reposListMock.mockResolvedValue({ org: "acme", total: Object.keys(caches).length, repos: Object.keys(caches).map(repo) });
  cacheListMock.mockImplementation((_o: string, r: string) =>
    caches[r] === undefined
      ? Promise.reject(new Error("rate limited"))
      : Promise.resolve({ repository: `acme/${r}`, total: caches[r].length, actions_caches: caches[r] }),
  );
}

const rowRepos = () => screen.getAllByRole("link").map((l) => l.textContent).filter((t) => !!t && ["api", "web", "docs", "idle"].includes(t));

describe("CachesPage", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("default_org", "acme");
    [tokensResolveMock, reposListMock, cacheListMock, cacheClearMock, jobsGetMock, orgsMineMock, toastInfo, toastSuccess, toastError].forEach((m) => m.mockReset());
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    cacheClearMock.mockResolvedValue({ queued: false, dry_run: true });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("asks for an org when none is selected and makes no calls", async () => {
    localStorage.clear();
    renderPage();
    expect(await screen.findByText(/No account selected yet/)).toBeInTheDocument();
    expect(reposListMock).not.toHaveBeenCalled();
  });

  it("ranks repositories by cache size, biggest first, and shows totals", async () => {
    seed({ api: [entry(10 * MB, 1)], web: [entry(300 * MB, 1)], docs: [entry(50 * MB, 1)] });
    renderPage();

    await screen.findByText("web");
    expect(rowRepos()).toEqual(["web", "docs", "api"]);
    expect(screen.getByText("360.0 MB total")).toBeInTheDocument();
    expect(cacheListMock).toHaveBeenCalledWith("acme", "web", "");
  });

  it("filters to repos with stale cache and ranks them by stale size", async () => {
    seed({ api: [entry(900 * MB, 1)], web: [entry(40 * MB, 20)], idle: [entry(5 * MB, 60), entry(5 * MB, 1)] });
    renderPage();
    await screen.findByText("api");

    fireEvent.click(screen.getByRole("checkbox", { name: /stale only/i }));

    await waitFor(() => expect(rowRepos()).toEqual(["web", "idle"]));
  });

  it("says a failed repo is missing instead of treating it as having no cache", async () => {
    seed({ api: [entry(MB, 1)] });
    reposListMock.mockResolvedValue({ org: "acme", total: 2, repos: [repo("api"), repo("web")] });
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("1 repository could not be loaded (web)");
  });

  it("runs a dry run for the selected repos only and changes nothing", async () => {
    seed({ api: [entry(10 * MB, 1)], web: [entry(20 * MB, 1)] });
    renderPage();
    await screen.findByText("web");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select web" }));
    fireEvent.click(screen.getByRole("button", { name: /dry run \(1\)/i }));

    await waitFor(() => expect(cacheClearMock).toHaveBeenCalledTimes(1));
    expect(cacheClearMock).toHaveBeenCalledWith("acme", "web", { token: "", dry_run: true });
    await waitFor(() => expect(toastInfo).toHaveBeenCalledWith(expect.stringContaining("20.0 MB would be deleted")));
  });

  it("confirms, enqueues one clear per selected repo, polls every job, then reloads the list", async () => {
    seed({ api: [entry(10 * MB, 1)], web: [entry(20 * MB, 1)] });
    let polls = 0;
    cacheClearMock.mockImplementation((_o: string, r: string) => Promise.resolve({ queued: true, dry_run: false, job_id: r === "api" ? 11 : 12 }));
    jobsGetMock.mockImplementation((id: number) => {
      polls += 1;
      return Promise.resolve({ id, status: polls > 2 ? "done" : "processing", result: null });
    });
    renderPage();
    await screen.findByText("web");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select all repositories" }));
    fireEvent.click(screen.getByRole("button", { name: /clear selected \(2\)/i }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("30.0 MB");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(cacheClearMock).toHaveBeenCalledTimes(2));
    expect(cacheClearMock).toHaveBeenCalledWith("acme", "api", { token: "", dry_run: false });
    expect(cacheClearMock).toHaveBeenCalledWith("acme", "web", { token: "", dry_run: false });
    await waitFor(() => expect(jobsGetMock).toHaveBeenCalledWith(11));
    expect(jobsGetMock).toHaveBeenCalledWith(12);

    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Cache cleared for 2 repos."), { timeout: 8000 });
    // The list is fetched again so it stops showing deleted caches, and the selection is dropped.
    await waitFor(() => expect(cacheListMock.mock.calls.length).toBeGreaterThan(2));
    expect(screen.getByRole("button", { name: /clear selected \(0\)/i })).toBeDisabled();
  }, 15000);

  it("reports failed clear jobs as failures, not success", async () => {
    seed({ api: [entry(MB, 1)] });
    cacheClearMock.mockResolvedValue({ queued: true, dry_run: false, job_id: 5 });
    jobsGetMock.mockResolvedValue({ id: 5, status: "failed", result: "boom" });
    renderPage();
    await screen.findByText("api");

    fireEvent.click(screen.getByRole("checkbox", { name: "Select api" }));
    fireEvent.click(screen.getByRole("button", { name: /clear selected \(1\)/i }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith(expect.stringContaining("1 of 1 cache clears failed")));
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it("lists at most five failed repos, then retries the load", async () => {
    const names = ["a", "b", "c", "d", "e", "f", "ok"];
    reposListMock.mockResolvedValue({ org: "acme", total: names.length, repos: names.map(repo) });
    cacheListMock.mockImplementation((_o: string, r: string) =>
      r === "ok" ? Promise.resolve({ repository: "acme/ok", total: 1, actions_caches: [entry(MB, 1)] }) : Promise.reject(new Error("nope")),
    );
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("6 repositories could not be loaded (a, b, c, d, e, …)");
    const before = cacheListMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(cacheListMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("shows the first error, with a retry, when every repo fails to load", async () => {
    seed({});
    reposListMock.mockResolvedValue({ org: "acme", total: 1, repos: [repo("api")] });
    renderPage();

    expect(await screen.findByText("rate limited")).toBeInTheDocument();
    const before = cacheListMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(cacheListMock.mock.calls.length).toBeGreaterThan(before));
  });

  it("shows the error, with a retry, when the repository list fails", async () => {
    reposListMock.mockRejectedValueOnce(new Error("repos down"));
    seed({ api: [entry(MB, 1)] });
    renderPage();

    expect(await screen.findByText("repos down")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("api")).toBeInTheDocument();
  });

  it("says so when no repository has any cache, or none has stale cache", async () => {
    seed({});
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });
    renderPage();
    expect(await screen.findByText("No repository has any Actions cache")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /stale only/i }));
    expect(await screen.findByText("No repository has stale cache")).toBeInTheDocument();
  });

  it("shows a dash for a repo with no cache entries and names the repos whose clear failed", async () => {
    seed({ api: [entry(MB, 1)], idle: [{ ...entry(MB, 1), last_accessed_at: null as unknown as string }] });
    cacheClearMock.mockImplementation((_o: string, r: string) =>
      r === "api" ? Promise.reject(new Error("forbidden")) : Promise.resolve({ queued: false, dry_run: true }),
    );
    renderPage();
    await screen.findByText("api");
    expect(screen.getByText("—")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select all repositories" }));
    fireEvent.click(screen.getByRole("button", { name: /dry run \(2\)/i }));

    await waitFor(() => expect(toastInfo).toHaveBeenCalledWith(expect.stringContaining("1 failed (api)")));
    // Only the repo whose dry run succeeded counts toward the bytes that "would be deleted".
    expect(toastInfo).toHaveBeenCalledWith(expect.stringContaining("(1.0 MB would be deleted)"));
  });

  it("hides selection and clear controls from a plain org member", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
    seed({ api: [entry(MB, 1)] });
    renderPage();
    await screen.findByText("api");

    expect(screen.queryByRole("checkbox", { name: "Select api" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /clear selected/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /dry run/i })).not.toBeInTheDocument();
  });
});
