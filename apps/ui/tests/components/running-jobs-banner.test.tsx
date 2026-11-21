import { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("@/components/ui/toast", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) } }));

import { RunningJobsBanner } from "@/components/running-jobs-banner";
import { listTrackedJobs, markLive, trackJob, unmarkLive } from "@/lib/running-jobs";

function renderBanner() {
  const queryClient = new QueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  render(
    <QueryClientProvider client={queryClient}>
      <RunningJobsBanner />
    </QueryClientProvider>,
  );
  return { invalidate };
}

// GET /jobs/{id} answers `status`; the first poll is 2 s after mount, so use short fake timers.
function stubJob(status: "done" | "failed", result: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ id: 5, job_type: "api.x", status, result }), { status: 200 }))),
  );
}

beforeEach(() => {
  localStorage.clear();
  toastSuccess.mockReset();
  toastError.mockReset();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("RunningJobsBanner (#1124)", () => {
  it("shows nothing when no job was left running", () => {
    renderBanner();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("re-attaches to a job started before a reload, then reports it and refreshes the data", async () => {
    trackJob({ jobId: 5, label: "security scan", org: "acme", startedAt: Date.now() });
    stubJob("done", "{}");
    const { invalidate } = renderBanner();

    expect(screen.getByRole("status")).toHaveTextContent("A security scan for acme is still running");

    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("The security scan for acme finished."));
    expect(invalidate).toHaveBeenCalled();
    expect(listTrackedJobs()).toEqual([]);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("reports a job that failed while the page was away", async () => {
    trackJob({ jobId: 5, label: "bulk fix", org: "acme", startedAt: Date.now() });
    stubJob("failed", "GitHub returned 403 for every repo.");
    renderBanner();

    await vi.advanceTimersByTimeAsync(2100);
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("The bulk fix for acme failed: GitHub returned 403 for every repo."));
    expect(listTrackedJobs()).toEqual([]);
  });

  it("leaves alone a job this page is already waiting on", () => {
    trackJob({ jobId: 5, label: "security scan", org: "acme", startedAt: Date.now() });
    markLive(5);
    try {
      renderBanner();
      expect(screen.queryByRole("status")).toBeNull();
    } finally {
      unmarkLive(5);
    }
  });

  it("still attaches the job under Strict Mode's setup, cleanup, setup (#1124)", async () => {
    stubJob("done", "{}");
    trackJob({ jobId: 5, label: "security scan", org: "acme", startedAt: Date.now() });
    render(
      <StrictMode>
        <QueryClientProvider client={new QueryClient()}>
          <RunningJobsBanner />
        </QueryClientProvider>
      </StrictMode>,
    );
    expect(await screen.findByText(/security scan for acme is still running/)).toBeInTheDocument();
  });
});
