import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const workflowsMock = vi.fn();
const runsMock = vi.fn();
const dispatchMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: vi.fn(),
    },
    automation: {
      workflows: (...args: unknown[]) => workflowsMock(...args),
      runs: (...args: unknown[]) => runsMock(...args),
      dispatch: (...args: unknown[]) => dispatchMock(...args),
    },
  },
}));

import AutomationPage from "@/app/automation/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AutomationPage />
    </QueryClientProvider>,
  );
}

describe("AutomationPage", () => {
  beforeEach(() => {
    tokensResolveMock.mockReset();
    workflowsMock.mockReset();
    runsMock.mockReset();
    dispatchMock.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders no results panel before any repository is loaded", () => {
    renderPage();
    expect(screen.queryByText("Workflows")).not.toBeInTheDocument();
    expect(workflowsMock).not.toHaveBeenCalled();
  });

  it("loads workflows and run history for the entered owner/repo", async () => {
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [
        { id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: "completed", last_run_conclusion: "success", last_run_at: "2026-07-20T00:00:00Z" },
      ],
    });
    runsMock.mockResolvedValue({
      repository: "acme/demo",
      runs: [
        { id: 100, name: "CI", status: "completed", conclusion: "success", head_branch: "main", created_at: "2026-07-20T00:00:00Z", duration_ms: 60000 },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. hello-world"), { target: { value: "demo" } });
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => {
      expect(workflowsMock).toHaveBeenCalledWith("acme", "demo", "");
      expect(runsMock).toHaveBeenCalledWith("acme", "demo", "");
    });

    await waitFor(() => {
      expect(screen.getAllByText("CI").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("main")).toBeInTheDocument();
  });

  it("arms then confirms a workflow dispatch, calling the API only after the second click", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [{ id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null }],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });
    dispatchMock.mockResolvedValue({ dispatched: true, message: "Workflow dispatched." });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. hello-world"), { target: { value: "demo" } });
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Dispatch/i }));

    await waitFor(() => {
      expect(screen.getByText("Dispatch workflow")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Dispatch workflow"));
    expect(dispatchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Confirm dispatch")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Confirm dispatch"));

    await waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledWith("acme", "demo", 1, { token: "", ref: "main" });
    });
  });

  it("surfaces an error message when loading workflows fails", async () => {
    workflowsMock.mockRejectedValue(new Error("GitHub API unreachable"));
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. hello-world"), { target: { value: "demo" } });
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => {
      expect(screen.getByText("GitHub API unreachable")).toBeInTheDocument();
    });
  });
});
