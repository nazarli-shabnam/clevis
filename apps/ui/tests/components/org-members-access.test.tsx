import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const accessMock = vi.fn();
const downloadMock = vi.fn();
let role: "admin" | "member" = "admin";

vi.mock("@/lib/download", () => ({ downloadTextFile: (...a: unknown[]) => downloadMock(...a) }));

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: { list: vi.fn().mockResolvedValue([]), revoke: vi.fn(), create: vi.fn() },
    collab: {
      members: vi.fn().mockResolvedValue({
        org: "acme",
        two_factor_overlay_available: true,
        members: [{ login: "octocat", avatar_url: "", role: "member", site_admin: false, two_factor_enabled: false }],
      }),
      outsideCollaborators: vi.fn(),
      invitations: vi.fn().mockResolvedValue({ org: "acme", invitations: [] }),
      permissionAudit: vi.fn(),
      inactiveMembers: vi.fn(),
      memberAccess: (...a: unknown[]) => accessMock(...a),
    },
    tokens: { resolve: vi.fn().mockRejectedValue(new Error("no saved token")) },
    orgs: { mine: () => Promise.resolve([{ org_login: "acme", role }]) },
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";
import { accessCsv } from "@/components/member-access-sheet";

const access = (over: Record<string, unknown> = {}) => ({
  org: "acme",
  login: "octocat",
  synced: true,
  activity_synced: true,
  is_member: true,
  role: "member",
  two_factor_enabled: false,
  last_event_at: "2026-01-02T00:00:00Z",
  last_push_at: "2026-01-01T00:00:00Z",
  last_push_repo: "acme/api",
  direct_grants: [{ repo: "acme/api", permission: "write", is_outside_collaborator: false, granted_at: "2026-01-01T00:00:00Z" }],
  ...over,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrgMembersPage />
    </QueryClientProvider>,
  );
}

describe("member access review", () => {
  beforeEach(() => {
    accessMock.mockReset();
    downloadMock.mockReset();
    role = "admin";
    accessMock.mockResolvedValue(access());
  });
  afterEach(cleanup);

  it("opens the drawer from a roster row and shows role, 2FA, grants and the caveat", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Review access for octocat" }));
    await waitFor(() => expect(screen.getByText("Access review: octocat")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/Direct repository access \(1\)/)).toBeInTheDocument());
    expect(accessMock).toHaveBeenCalledWith("acme", "octocat");
    expect(screen.getByText("Not enabled")).toBeInTheDocument();
    expect(screen.getByText(/can understate what the person can reach/)).toBeInTheDocument();
  });

  it("explains when the org has not synced yet", async () => {
    accessMock.mockResolvedValue(access({ synced: false, direct_grants: [] }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Review access for octocat" }));
    await waitFor(() => expect(screen.getByText(/first membership sync/)).toBeInTheDocument());
  });

  it("says activity is unavailable, not none, until the activity backfill has run", async () => {
    accessMock.mockResolvedValue(access({ activity_synced: false, last_push_at: null, last_event_at: null, last_push_repo: null }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Review access for octocat" }));
    await waitFor(() => expect(screen.getByText(/not evidence the account is dormant/)).toBeInTheDocument());
    expect(screen.queryByText("None recorded")).not.toBeInTheDocument();
  });

  it("exports the review as CSV", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Review access for octocat" }));
    fireEvent.click(await screen.findByRole("button", { name: /Export for review/ }));
    expect(downloadMock).toHaveBeenCalledWith("clevis-access-acme-octocat.csv", expect.stringContaining("acme/api"), "text/csv");
  });

  it("shows an error with retry when the lookup fails", async () => {
    accessMock.mockRejectedValue(new Error("nope"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Review access for octocat" }));
    await waitFor(() => expect(screen.getByText("nope")).toBeInTheDocument());
  });

  it("does not offer the review to a plain member", async () => {
    role = "member";
    renderPage();
    await screen.findByText("octocat");
    expect(screen.queryByRole("button", { name: /Review access/ })).not.toBeInTheDocument();
  });
});

describe("accessCsv", () => {
  it("writes one row per grant, or one row for the person when there are none", () => {
    expect(accessCsv(access() as never).split("\r\n")).toHaveLength(2);
    const none = accessCsv(access({ direct_grants: [], two_factor_enabled: null }) as never).split("\r\n");
    expect(none).toHaveLength(2);
    expect(none[1]).toContain("unknown");
    expect(none[0]).toContain("Last activity");
    expect(none[1]).toContain("Direct grants only");
  });
});
