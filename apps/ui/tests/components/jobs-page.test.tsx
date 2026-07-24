import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const jobsListMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    jobs: { list: (...args: unknown[]) => jobsListMock(...args) },
  },
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

import JobsPage from "@/app/jobs/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <JobsPage />
    </QueryClientProvider>,
  );
}

describe("JobsPage", () => {
  beforeEach(() => {
    jobsListMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders jobs", async () => {
    jobsListMock.mockResolvedValue([
      { id: 1, job_type: "github.clear_actions_cache", status: "done", result: "ok", created_at: "2026-01-01T00:00:00Z" },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByText("github.clear_actions_cache")).toBeInTheDocument());
  });

  it("shows a retry option instead of a fake empty state when the query fails", async () => {
    // Regression test: this page used to default jobs to [] on any query error and never
    // checked isError, so a real 403/500 rendered identically to "genuinely zero rows".
    jobsListMock.mockRejectedValue(new Error("Workspace admin access required"));
    renderPage();
    await waitFor(() => expect(screen.getByText("Workspace admin access required")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText(/No jobs/)).not.toBeInTheDocument();
  });

  it("shows the empty state only when the query genuinely succeeds with no rows", async () => {
    jobsListMock.mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No jobs/)).toBeInTheDocument());
  });
});
