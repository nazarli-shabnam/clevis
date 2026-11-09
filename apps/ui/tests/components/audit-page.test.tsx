import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auditListMock = vi.fn();
const auditActionsMock = vi.fn();
const jobsListMock = vi.fn();
const downloadTextFileMock = vi.fn();

vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));

let mockSearchParams = new URLSearchParams();

// jsdom doesn't implement scrollIntoView -- needed for the ?job_id= highlight-and-scroll effect.
Element.prototype.scrollIntoView = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
}));

// Workspace admin by default; the non-admin test flips it.
let mockIsWorkspaceAdmin = true;
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { is_workspace_admin: mockIsWorkspaceAdmin } }),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    audit: {
      list: (...args: unknown[]) => auditListMock(...args),
      actions: (...args: unknown[]) => auditActionsMock(...args),
    },
    jobs: { list: (...args: unknown[]) => jobsListMock(...args) },
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
    auditActionsMock.mockReset();
    auditActionsMock.mockResolvedValue([]);
    mockIsWorkspaceAdmin = true;
    jobsListMock.mockReset();
    jobsListMock.mockResolvedValue([]);
    downloadTextFileMock.mockReset();
    mockSearchParams = new URLSearchParams();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("builds the action filter from the actions the API reports, not a hard-coded list (#664)", async () => {
    auditListMock.mockResolvedValue([]);
    auditActionsMock.mockResolvedValue(["config.update", "token.save"]);
    renderPage();

    await waitFor(() => expect(screen.getByRole("option", { name: "token.save" })).toBeInTheDocument());
    expect(screen.getByRole("option", { name: "config.update" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "all actions" })).toBeInTheDocument();
    // The old hard-coded entry that no code path ever writes is gone.
    expect(screen.queryByRole("option", { name: "cache.clear" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Filter by action"), { target: { value: "token.save" } });
    await waitFor(() => expect(auditListMock).toHaveBeenLastCalledWith(expect.objectContaining({ action: "token.save", limit: 100 })));
  });

  it("still offers only the all-actions option when the actions request fails (#664)", async () => {
    auditListMock.mockResolvedValue([]);
    auditActionsMock.mockRejectedValue(new Error("boom"));
    mockSearchParams = new URLSearchParams();
    renderPage();

    // Only "all actions" is offered, and the page still works.
    await waitFor(() => expect(auditActionsMock).toHaveBeenCalled());
    expect(screen.getByRole("option", { name: "all actions" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });

  it("tells a non-admin the log is admins-only and makes no requests (#664)", async () => {
    mockIsWorkspaceAdmin = false;
    renderPage();

    expect(screen.getByText(/only available to workspace admins/i)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 30));
    expect(auditListMock).not.toHaveBeenCalled();
    expect(auditActionsMock).not.toHaveBeenCalled();
    expect(jobsListMock).not.toHaveBeenCalled();
  });

  it("renders audit log entries", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByText("installation.connected")).toBeInTheDocument());
  });

  it("exports the shown events as CSV with ISO timestamps and the job status", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "cache.clear.queued", target: "acme/api", payload: JSON.stringify({ job_id: 7 }), created_at: "2026-01-01T00:00:00Z" },
      { id: 2, actor: "v@e.com", action: "token.save", target: "acme", payload: "{}", created_at: "2026-01-02T00:00:00Z" },
    ]);
    jobsListMock.mockResolvedValue([{ id: 7, job_type: "github.clear_actions_cache", status: "done", result: null, created_at: "", updated_at: "" }]);
    renderPage();

    // the job list loads separately from the log, so wait for it before exporting
    await waitFor(() => expect(screen.getAllByText("done").length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("button", { name: /export csv/i }));

    const [filename, csv, mime] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-audit-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(mime).toBe("text/csv");
    expect(csv.split("\r\n")).toEqual([
      "Actor,Action,Target,Job status,Time",
      "u@e.com,cache.clear.queued,acme/api,done,2026-01-01T00:00:00Z",
      "v@e.com,token.save,acme,,2026-01-02T00:00:00Z",
    ]);
  });

  it("exports 'unknown' (not a blank) for a job status that can't be looked up, and says how many events it holds", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "cache.clear.queued", target: "acme/api", payload: JSON.stringify({ job_id: 99 }), created_at: "2026-01-01T00:00:00Z" },
    ]);
    jobsListMock.mockResolvedValue([]); // job 99 isn't in the (capped) job list
    renderPage();

    expect(await screen.findByText(/Exports the 1 loaded events\./)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    expect(downloadTextFileMock.mock.calls[0][1].split("\r\n")[1]).toBe("u@e.com,cache.clear.queued,acme/api,unknown,2026-01-01T00:00:00Z");
  });

  it("shows a retry option instead of a fake empty state when the query fails", async () => {
    // A real 403/500 must not render identically to "genuinely zero rows".
    auditListMock.mockRejectedValue(new Error("Workspace admin access required"));
    renderPage();
    await waitFor(() => expect(screen.getByText("Workspace admin access required")).toBeInTheDocument());
    expect(screen.queryByText(/No audit events/)).not.toBeInTheDocument();
    // A misleading "0 entries" chip must not render alongside the error.
    expect(screen.queryByText(/entries$/)).not.toBeInTheDocument();

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

  it("shows a row's live job status when its payload embeds a matching job_id", async () => {
    auditListMock.mockResolvedValue([
      {
        id: 1,
        actor: "u@e.com",
        action: "cache.clear.queued",
        target: "acme/demo",
        payload: JSON.stringify({ job_id: 42, key: null, ref: null, dry_run: false }),
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    jobsListMock.mockResolvedValue([
      { id: 42, job_type: "github.clear_actions_cache", status: "processing", result: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("processing")).toBeInTheDocument());
  });

  it("shows a dash for rows with no job_id, or whose job_id isn't in the jobs list", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
      { id: 2, actor: "u@e.com", action: "cache.clear.queued", target: "acme/demo", payload: JSON.stringify({ job_id: 99 }), created_at: "2026-01-01T00:00:00Z" },
    ]);
    jobsListMock.mockResolvedValue([]);

    renderPage();

    // "cache.clear.queued" is also a static <option>, so waiting on it would false-positive.
    await waitFor(() => expect(screen.getByText("2 entries")).toBeInTheDocument());
    expect(screen.getAllByText("—").length).toBe(2);
  });

  it("shows a distinct 'failed to load' indicator, not a plain dash, when the jobs query fails for a row with a job_id", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "cache.clear.queued", target: "acme/demo", payload: JSON.stringify({ job_id: 42 }), created_at: "2026-01-01T00:00:00Z" },
    ]);
    jobsListMock.mockRejectedValue(new Error("GitHub API unreachable"));

    renderPage();

    await waitFor(() => expect(screen.getByText("1 entries")).toBeInTheDocument());
    expect(screen.getByText("failed to load")).toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
  });

  it("still shows a plain dash for rows with no job_id even when the jobs query fails", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
    ]);
    jobsListMock.mockRejectedValue(new Error("GitHub API unreachable"));

    renderPage();

    await waitFor(() => expect(screen.getByText("1 entries")).toBeInTheDocument());
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("does not crash on a malformed (non-JSON) payload", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "cache.clear.queued", target: "acme/demo", payload: "not json", created_at: "2026-01-01T00:00:00Z" },
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("1 entries")).toBeInTheDocument());
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("sorts rows by clicking a sortable column header", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "bravo@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
      { id: 2, actor: "alpha@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-02T00:00:00Z" },
    ]);
    renderPage();

    await waitFor(() => expect(screen.getByText("2 entries")).toBeInTheDocument());
    const rowsBefore = screen.getAllByRole("row").slice(1);
    expect(rowsBefore[0]).toHaveTextContent("bravo@e.com");

    fireEvent.click(within(screen.getByRole("table")).getByText("Actor"));

    const rowsAfterAsc = screen.getAllByRole("row").slice(1);
    expect(rowsAfterAsc[0]).toHaveTextContent("alpha@e.com");

    fireEvent.click(within(screen.getByRole("table")).getByText("Actor"));

    const rowsAfterDesc = screen.getAllByRole("row").slice(1);
    expect(rowsAfterDesc[0]).toHaveTextContent("bravo@e.com");
  });

  it("shows a Load more button on a full page and requests the next page below the last id seen", async () => {
    const fullPage = Array.from({ length: 100 }, (_, i) => ({
      id: 200 - i,
      actor: "u@e.com",
      action: "installation.connected",
      target: "acme",
      payload: "{}",
      created_at: "2026-01-01T00:00:00Z",
    }));
    auditListMock.mockResolvedValueOnce(fullPage);
    auditListMock.mockResolvedValueOnce([
      { id: 100, actor: "u@e.com", action: "older.event", target: "acme", payload: "{}", created_at: "2025-12-01T00:00:00Z" },
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("100 entries")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /load more/i }));

    // The cursor is the oldest id loaded (101), and the next page is appended, not swapped in.
    await waitFor(() => expect(auditListMock).toHaveBeenLastCalledWith(expect.objectContaining({ before_id: 101, limit: 100 })));
    await waitFor(() => expect(screen.getByText("101 entries")).toBeInTheDocument());
    expect(screen.getByText("older.event")).toBeInTheDocument();
    // A short page means the log is exhausted, so there is nothing more to load.
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it("keeps the loaded rows on screen and shows 'Loading…' on the button while the next page fetches (#768)", async () => {
    const fullPage = Array.from({ length: 100 }, (_, i) => ({
      id: 200 - i,
      actor: "u@e.com",
      action: "installation.connected",
      target: "acme",
      payload: "{}",
      created_at: "2026-01-01T00:00:00Z",
    }));
    let resolveNext: (rows: typeof fullPage) => void = () => {};
    auditListMock.mockResolvedValueOnce(fullPage);
    auditListMock.mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve }));

    renderPage();
    await waitFor(() => expect(screen.getByText("100 entries")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /load more/i }));

    expect(await screen.findByRole("button", { name: /loading/i })).toBeDisabled();
    expect(screen.getByText("100 entries")).toBeInTheDocument();

    resolveNext([]);
    await waitFor(() => expect(screen.queryByRole("button", { name: /loading/i })).not.toBeInTheDocument());
  });

  it("applies the actor, target and date filters on submit, not per keystroke", async () => {
    auditListMock.mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(auditListMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Actor"), { target: { value: " ada@example.com " } });
    fireEvent.change(screen.getByLabelText("Target contains"), { target: { value: "acme/api" } });
    fireEvent.change(screen.getByLabelText("From (UTC)"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("To (UTC)"), { target: { value: "2026-03-05" } });
    expect(auditListMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));

    // "To" is inclusive, so the exclusive upper bound is the start of the next UTC day.
    await waitFor(() =>
      expect(auditListMock).toHaveBeenLastCalledWith({
        actor: "ada@example.com",
        target: "acme/api",
        since: "2026-03-01T00:00:00Z",
        until: "2026-03-06T00:00:00.000Z",
        action: undefined,
        before_id: undefined,
        limit: 100,
      }),
    );
    expect(await screen.findByText(/No audit events matching "the applied filters"/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(auditListMock).toHaveBeenLastCalledWith({ action: undefined, before_id: undefined, limit: 100 }));
    expect(screen.getByLabelText("Actor")).toHaveValue("");
  });

  it("filters by exact action only; the action-prefix field is the org view's", async () => {
    auditListMock.mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(auditListMock).toHaveBeenCalled());
    expect(screen.queryByLabelText("Action starts with")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Filter by action")).toBeInTheDocument();
  });

  it("does not show a Load more button when fewer rows than the limit come back", async () => {
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
    ]);
    renderPage();

    await waitFor(() => expect(screen.getByText("1 entries")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it("highlights the row matching a ?job_id= deep link, e.g. from the cache panel's 'View in Audit Log' link", async () => {
    mockSearchParams = new URLSearchParams("job_id=42");
    auditListMock.mockResolvedValue([
      { id: 1, actor: "u@e.com", action: "cache.clear.queued", target: "acme/demo", payload: JSON.stringify({ job_id: 42 }), created_at: "2026-01-01T00:00:00Z" },
      { id: 2, actor: "u@e.com", action: "installation.connected", target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" },
    ]);
    jobsListMock.mockResolvedValue([
      { id: 42, job_type: "github.clear_actions_cache", status: "done", result: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
    ]);

    renderPage();

    await waitFor(() => expect(screen.getByText("done")).toBeInTheDocument());
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveClass("ring-primary/40");
    expect(rows[1]).not.toHaveClass("ring-primary/40");
  });
});
