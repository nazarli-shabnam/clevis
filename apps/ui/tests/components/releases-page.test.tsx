import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const releaseTimelineMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: { resolve: (...args: unknown[]) => tokensResolveMock(...args) },
    github: { releaseTimeline: (...args: unknown[]) => releaseTimelineMock(...args) },
  },
}));

const downloadTextFileMock = vi.fn();
vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));

import ReleasesPage from "@/app/releases/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ReleasesPage />
    </QueryClientProvider>,
  );
}

const RELEASE = {
  repo: "acme/api",
  tag_name: "v1.2.0",
  name: "v1.2.0",
  published_at: "2026-07-20T00:00:00Z",
  is_prerelease: false,
  body_preview: "Bug fixes",
  url: "https://github.com/acme/api/releases/tag/v1.2.0",
};

describe("ReleasesPage", () => {
  beforeEach(() => {
    tokensResolveMock.mockReset();
    releaseTimelineMock.mockReset();
    downloadTextFileMock.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows a no-default-org message when nothing is configured", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/No account selected/)).toBeInTheDocument();
    });
    expect(releaseTimelineMock).not.toHaveBeenCalled();
  });

  it("explains the page is org-only and skips the release call for a personal scope", async () => {
    localStorage.setItem("active_scope", JSON.stringify({ kind: "personal", login: "octocat" }));
    renderPage();
    expect(await screen.findByText(/lists organization repositories/)).toBeInTheDocument();
    expect(releaseTimelineMock).not.toHaveBeenCalled();
  });

  it("renders releases for the default org at the default 90-day window", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockResolvedValue({ org: "acme", releases: [RELEASE] });

    renderPage();

    await waitFor(() => expect(screen.getAllByText(/v1\.2\.0/).length).toBeGreaterThan(0));
    expect(releaseTimelineMock).toHaveBeenCalledWith("acme", "", 90);
  });

  it("shows a pre-release badge for pre-release entries", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockResolvedValue({ org: "acme", releases: [{ ...RELEASE, is_prerelease: true }] });

    renderPage();

    await waitFor(() => expect(screen.getByText("pre-release")).toBeInTheDocument());
  });

  it("re-queries with the new day window when the selector changes", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockResolvedValue({ org: "acme", releases: [] });

    renderPage();

    await waitFor(() => expect(releaseTimelineMock).toHaveBeenCalledWith("acme", "", 90));

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "30" } });

    await waitFor(() => expect(releaseTimelineMock).toHaveBeenCalledWith("acme", "", 30));
  });

  it("shows a retry option instead of a fake empty state when the query fails", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockRejectedValue(new Error("Workspace admin access required"));

    renderPage();

    await waitFor(() => expect(screen.getByText("Workspace admin access required")).toBeInTheDocument());
    expect(screen.queryByText(/No releases/)).not.toBeInTheDocument();

    releaseTimelineMock.mockResolvedValueOnce({ org: "acme", releases: [] });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText(/No releases/)).toBeInTheDocument());
    expect(releaseTimelineMock).toHaveBeenCalledTimes(2);
  });

  it("falls back to a generic message when the rejection isn't an Error instance", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockRejectedValue("boom");

    renderPage();

    await waitFor(() => expect(screen.getByText("Failed to load releases.")).toBeInTheDocument());
  });

  it("still queries via a connected GitHub App installation when there's no saved PAT to resolve", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockRejectedValue(new Error("No saved token for this org"));
    releaseTimelineMock.mockResolvedValue({ org: "acme", releases: [RELEASE] });

    renderPage();

    await waitFor(() => expect(screen.getAllByText(/v1\.2\.0/).length).toBeGreaterThan(0));
    expect(releaseTimelineMock).toHaveBeenCalledWith("acme", "", 90);
  });

  it("shows the release-timeline endpoint's own error when neither a saved token nor an installation is available", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockRejectedValue(new Error("No saved token for this org"));
    releaseTimelineMock.mockRejectedValue(
      new Error("No GitHub App installation found for 'acme' and no token was provided."),
    );

    renderPage();

    await waitFor(() =>
      expect(
        screen.getByText("No GitHub App installation found for 'acme' and no token was provided."),
      ).toBeInTheDocument(),
    );
  });

  it("shows the empty state only when the query genuinely succeeds with no rows", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockResolvedValue({ org: "acme", releases: [] });

    renderPage();

    await waitFor(() => expect(screen.getByText(/No releases in the last 90 days/)).toBeInTheDocument());
  });

  it("exports the releases as CSV, including the pre-release flag and link", async () => {
    localStorage.setItem("default_org", "acme");
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    releaseTimelineMock.mockResolvedValue({
      org: "acme",
      releases: [RELEASE, { ...RELEASE, name: "=cmd()", tag_name: "v2.0.0-rc1", is_prerelease: true, url: "https://github.com/acme/api/releases/tag/v2" }],
    });

    renderPage();

    // each sortable header runs its sortValue
    fireEvent.click(await screen.findByRole("button", { name: /^Repository/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Release/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Tag/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Published/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Type/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Export CSV/ }));
    const [filename, csv] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-releases-acme-90d-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(csv).toContain("Repository,Release,Tag,Notes,Published,Type,Link");
    expect(csv).toContain("pre-release");
    expect(csv).toContain("https://github.com/acme/api/releases/tag/v1.2.0");
    // a name that looks like a formula is neutralised by the shared CSV helper
    expect(csv).not.toMatch(/(^|,)=cmd/m);
  });
});
