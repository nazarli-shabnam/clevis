import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auditListMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    audit: { list: (...args: unknown[]) => auditListMock(...args) },
  },
}));

import AuditPage from "@/app/audit/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuditPage />
    </QueryClientProvider>,
  );
}

describe("AuditPage", () => {
  beforeEach(() => {
    auditListMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders audit log entries", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByText("installation.connected")).toBeInTheDocument());
  });

  it("shows a retry option instead of a fake empty state when the query fails", async () => {
    // Regression test: this page used to default logs to [] on any query error and never
    // checked isError, so a real 403/500 rendered identically to "genuinely zero rows".
    auditListMock.mockRejectedValue(new Error("Workspace admin access required"));
    renderPage();
    await waitFor(() => expect(screen.getByText("Workspace admin access required")).toBeInTheDocument());
    expect(screen.queryByText(/No audit events/)).not.toBeInTheDocument();

    auditListMock.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByText(/No audit events/)).toBeInTheDocument());
    expect(auditListMock).toHaveBeenCalledTimes(2);
  });

  it("falls back to a generic message when the rejection isn't an Error instance", async () => {
    auditListMock.mockRejectedValue("boom");
    renderPage();
    await waitFor(() => expect(screen.getByText("Failed to load audit events.")).toBeInTheDocument());
  });

  it("shows the empty state only when the query genuinely succeeds with no rows", async () => {
    auditListMock.mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No audit events/)).toBeInTheDocument());
  });
});
