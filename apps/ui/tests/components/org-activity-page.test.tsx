import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auditMock = vi.fn();
const jobsMock = vi.fn();
const mineMock = vi.fn();
const downloadMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
}));

vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadMock(...args),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: () => mineMock() },
    audit: { listForOrg: (...args: unknown[]) => auditMock(...args) },
    jobs: { listForOrg: (...args: unknown[]) => jobsMock(...args) },
  },
}));

import OrgActivityPage from "@/app/settings/org/[login]/activity/page";
import { toAuditFilters } from "@/lib/audit-filters";

function row(id: number, action = "token.saved") {
  return { id, actor: "a@e.com", action, target: "acme", payload: "{}", created_at: "2026-01-01T00:00:00Z" };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrgActivityPage />
    </QueryClientProvider>,
  );
}

describe("toAuditFilters", () => {
  it("omits blanks, trims text and makes `to` an inclusive UTC day", () => {
    expect(toAuditFilters({ actionPrefix: " ", actor: "", target: "", from: "", to: "" })).toEqual({});
    expect(
      toAuditFilters({ actionPrefix: " token. ", actor: "a@e.com", target: "x", from: "2026-01-02", to: "2026-01-31" }),
    ).toEqual({
      action_prefix: "token.",
      actor: "a@e.com",
      target: "x",
      since: "2026-01-02T00:00:00Z",
      until: "2026-02-01T00:00:00.000Z",
    });
  });
});

describe("OrgActivityPage", () => {
  beforeEach(() => {
    auditMock.mockReset();
    jobsMock.mockReset();
    mineMock.mockReset();
    downloadMock.mockReset();
    mineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    auditMock.mockResolvedValue([row(2), row(1, "job.enqueued")]);
    jobsMock.mockResolvedValue([
      { id: 9, job_type: "github.backfill_repo_events", status: "failed", result: "boom", updated_at: "2026-01-01T00:00:00Z" },
    ]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows audit rows and jobs to an admin", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("job.enqueued")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("github.backfill_repo_events")).toBeInTheDocument());
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("explains instead of querying when the caller is only a member", async () => {
    mineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/limited to organization admins/)).toBeInTheDocument());
    expect(auditMock).not.toHaveBeenCalled();
    expect(jobsMock).not.toHaveBeenCalled();
  });

  it("explains when the caller has no membership in the org", async () => {
    mineMock.mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/needs an admin membership/)).toBeInTheDocument());
    expect(auditMock).not.toHaveBeenCalled();
  });

  it("applies filters only on submit", async () => {
    renderPage();
    await waitFor(() => expect(auditMock).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByLabelText("Action starts with"), { target: { value: "token." } });
    expect(auditMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() =>
      expect(auditMock).toHaveBeenLastCalledWith("acme", expect.objectContaining({ action_prefix: "token." })),
    );
  });

  it("pages older rows with before_id when a full page came back", async () => {
    const full = Array.from({ length: 100 }, (_, i) => row(500 - i));
    auditMock.mockResolvedValueOnce(full).mockResolvedValueOnce([row(400)]);
    renderPage();
    const buttons = await screen.findAllByRole("button", { name: "Load more" });
    fireEvent.click(buttons[0]);
    await waitFor(() => expect(auditMock).toHaveBeenCalledTimes(2));
    expect(auditMock.mock.calls[1][1]).toEqual(expect.objectContaining({ before_id: 401 }));
  });

  it("exports the loaded rows as CSV", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("job.enqueued")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Export CSV/ }));
    expect(downloadMock).toHaveBeenCalledTimes(1);
    const [name, csv] = downloadMock.mock.calls[0];
    expect(name).toMatch(/^clevis-activity-acme-/);
    expect(csv).toContain("job.enqueued");
  });

  it("shows a retry state when the audit query fails", async () => {
    auditMock.mockRejectedValue(new Error("nope"));
    renderPage();
    await waitFor(() => expect(screen.getByText(/Couldn't load the audit log: nope/)).toBeInTheDocument());
  });
});
