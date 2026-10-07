import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const accessMock = vi.fn();
const downloadMock = vi.fn();

vi.mock("@/lib/download", () => ({ downloadTextFile: (...a: unknown[]) => downloadMock(...a) }));
vi.mock("@/lib/api/client", () => ({
  api: { collab: { memberAccess: (...a: unknown[]) => accessMock(...a) } },
}));

import { MemberAccessSheet, accessCsv } from "@/components/member-access-sheet";
import type { MemberAccess } from "@/lib/api/types";

const GRANT = { repo: "acme/api", permission: "write", is_outside_collaborator: false, granted_at: "2026-01-01T00:00:00Z" };

function access(over: Partial<MemberAccess> = {}): MemberAccess {
  return {
    org: "acme",
    login: "octocat",
    synced: true,
    activity_synced: true,
    is_member: true,
    role: "member",
    two_factor_enabled: true,
    last_event_at: new Date(Date.now() - 3 * 86400_000).toISOString(),
    last_push_at: new Date(Date.now() - 5 * 86400_000).toISOString(),
    last_push_repo: "acme/api",
    direct_grants: [GRANT],
    ...over,
  };
}

function renderSheet(login: string | null = "octocat", onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (l: string | null) => (
    <QueryClientProvider client={qc}>
      <MemberAccessSheet orgLogin="acme" login={l} onClose={onClose} />
    </QueryClientProvider>
  );
  const utils = render(tree(login));
  return { ...utils, setLogin: (l: string | null) => utils.rerender(tree(l)), onClose };
}

beforeEach(() => {
  accessMock.mockReset();
  downloadMock.mockReset();
});
afterEach(cleanup);

describe("accessCsv", () => {
  const rows = (a: MemberAccess) => accessCsv(a).split("\r\n");

  it("spells out yes/no for a fully known person and one grant", () => {
    const [, line] = rows(access({ direct_grants: [{ ...GRANT, is_outside_collaborator: true }] }));
    expect(line).toContain("octocat,yes,member,yes");
    expect(line).toContain("acme/api,write,yes");
  });

  it("writes 'no' for a non-member without 2FA and an insider grant", () => {
    const [, line] = rows(access({ is_member: false, two_factor_enabled: false }));
    expect(line).toContain("octocat,no,member,no");
    expect(line).toContain("acme/api,write,no");
  });

  it("writes 'unknown' where GitHub didn't say, and 'not synced' before the activity import", () => {
    const [, line] = rows(
      access({
        is_member: null,
        role: null,
        two_factor_enabled: null,
        activity_synced: false,
        direct_grants: [{ ...GRANT, is_outside_collaborator: null }],
      }),
    );
    expect(line).toContain("octocat,unknown,,unknown,not synced,not synced,acme/api,write,unknown");
  });

  it("says 'none recorded' when activity is synced but empty, and gives one row when there are no grants", () => {
    const all = rows(access({ last_push_at: null, last_event_at: null, direct_grants: [] }));
    expect(all).toHaveLength(2);
    expect(all[1]).toContain("none recorded,none recorded,,,");
  });
});

describe("MemberAccessSheet", () => {
  it("stays closed and makes no request without a person", () => {
    renderSheet(null);
    expect(screen.queryByText(/Access review/)).toBeNull();
    expect(accessMock).not.toHaveBeenCalled();
  });

  it("shows a loading state, then role, 2FA, activity and the direct grants", async () => {
    accessMock.mockResolvedValue(access());
    renderSheet();

    expect(screen.getByText("Access review: octocat")).toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByText(/5 days ago in acme\/api/)).toBeInTheDocument();
    expect(screen.getByText("3 days ago")).toBeInTheDocument();
    expect(screen.getByText("Direct repository access (1)")).toBeInTheDocument();
    expect(screen.getByText("write")).toBeInTheDocument();
  });

  it("marks an outside collaborator's grant", async () => {
    accessMock.mockResolvedValue(access({ direct_grants: [{ ...GRANT, is_outside_collaborator: true }] }));
    renderSheet();

    expect(await screen.findByText(/write · outside/)).toBeInTheDocument();
  });

  it("spells out non-members, missing 2FA and absent activity instead of showing blanks", async () => {
    accessMock.mockResolvedValue(
      access({ is_member: false, role: null, two_factor_enabled: false, last_push_at: null, last_event_at: null, direct_grants: [] }),
    );
    renderSheet();

    expect(await screen.findByText("No (not in the org roster)")).toBeInTheDocument();
    expect(screen.getByText("Not enabled")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getAllByText("None recorded")).toHaveLength(2);
    expect(screen.getByText("No direct grants recorded.")).toBeInTheDocument();
  });

  it("says 'Unknown' for membership and 2FA that GitHub didn't report", async () => {
    accessMock.mockResolvedValue(access({ is_member: null, two_factor_enabled: null }));
    renderSheet();

    expect(await screen.findAllByText("Unknown")).toHaveLength(2);
  });

  it("warns that an empty last-activity isn't evidence of dormancy until the activity import finishes", async () => {
    accessMock.mockResolvedValue(access({ activity_synced: false, last_push_at: null, last_event_at: null }));
    renderSheet();

    expect(await screen.findAllByText("Not available yet")).toHaveLength(2);
    expect(screen.getByText(/Activity history is still being imported/)).toBeInTheDocument();
  });

  it("explains an org whose first membership sync hasn't finished", async () => {
    accessMock.mockResolvedValue(access({ synced: false }));
    renderSheet();

    expect(await screen.findByText(/first membership sync to finish/)).toBeInTheDocument();
    expect(screen.queryByText(/Direct repository access/)).toBeNull();
  });

  it("shows a retryable error", async () => {
    accessMock.mockRejectedValueOnce(new Error("lookup failed"));
    renderSheet();

    expect(await screen.findByText("lookup failed")).toBeInTheDocument();
    accessMock.mockResolvedValue(access());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Enabled")).toBeInTheDocument();
  });

  it("downloads the review as a CSV named for the org and person", async () => {
    accessMock.mockResolvedValue(access());
    renderSheet();

    fireEvent.click(await screen.findByRole("button", { name: /export for review/i }));

    expect(downloadMock).toHaveBeenCalledTimes(1);
    const [name, body, type] = downloadMock.mock.calls[0];
    expect(name).toBe("clevis-access-acme-octocat.csv");
    expect(body).toContain("Login,Org member,Role,2FA");
    expect(type).toBe("text/csv");
  });

  it("closes without asking for anyone, then reopens for the next person", async () => {
    accessMock.mockResolvedValue(access());
    const { setLogin } = renderSheet("octocat");
    await screen.findByText("Enabled");

    setLogin(null);
    await waitFor(() => expect(screen.queryByText("Access review: octocat")).toBeNull());

    accessMock.mockResolvedValue(access({ login: "hubot" }));
    setLogin("hubot");
    await waitFor(() => expect(screen.getByText("Access review: hubot")).toBeInTheDocument());
    expect(accessMock).toHaveBeenLastCalledWith("acme", "hubot");
  });
});
