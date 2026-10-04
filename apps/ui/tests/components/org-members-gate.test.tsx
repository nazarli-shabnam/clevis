import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const mineMock = vi.fn();
const listMock = vi.fn();
const rosterMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: (...args: unknown[]) => mineMock(...args) },
    invitations: {
      list: (...args: unknown[]) => listMock(...args),
      revoke: vi.fn(),
      create: vi.fn(),
    },
    collab: {
      members: (...args: unknown[]) => rosterMock(...args),
      outsideCollaborators: vi.fn(),
      invitations: vi.fn(),
      permissionAudit: vi.fn(),
      inactiveMembers: vi.fn(),
    },
    tokens: { resolve: vi.fn().mockRejectedValue(new Error("no saved token")) },
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrgMembersPage />
    </QueryClientProvider>,
  );
}

describe("OrgMembersPage admin gate", () => {
  beforeEach(() => {
    mineMock.mockReset();
    listMock.mockReset();
    rosterMock.mockReset();
    rosterMock.mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: false });
    listMock.mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
  });

  it("shows the invite form and invitations to an org admin", async () => {
    mineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    renderPage();

    expect(await screen.findByRole("button", { name: /send invite/i })).toBeInTheDocument();
    expect(await screen.findByText("No invitations yet")).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledWith("acme");
  });

  it("matches the org login case-insensitively", async () => {
    mineMock.mockResolvedValue([{ org_login: "ACME", role: "admin" }]);
    renderPage();

    expect(await screen.findByRole("button", { name: /send invite/i })).toBeInTheDocument();
  });

  it("explains instead of rendering the invite UI to a plain member, and never calls the admin-only list", async () => {
    mineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
    renderPage();

    expect(await screen.findByText(/limited to organization admins/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send invite/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Clevis workspace invitations")).not.toBeInTheDocument();
    expect(screen.queryByText("No invitations yet")).not.toBeInTheDocument();
    expect(listMock).not.toHaveBeenCalled();
  });

  it("keeps the member-level roster visible to a plain member", async () => {
    mineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
    renderPage();

    await screen.findByText(/limited to organization admins/);
    expect(screen.getByText("GitHub organization roster")).toBeInTheDocument();
    await waitFor(() => expect(rosterMock).toHaveBeenCalled());
  });

  it("explains when the caller has no membership in this org at all", async () => {
    mineMock.mockResolvedValue([{ org_login: "other", role: "admin" }]);
    renderPage();

    expect(await screen.findByText(/needs an admin membership/)).toBeInTheDocument();
    expect(listMock).not.toHaveBeenCalled();
  });

  it("holds the admin UI back while the role is unknown, so a member never triggers the admin-only list", async () => {
    let resolveMine!: (v: { org_login: string; role: "member" }[]) => void;
    mineMock.mockReturnValue(new Promise((res) => { resolveMine = res; }));
    renderPage();

    expect(await screen.findByText("Checking your access…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send invite/i })).not.toBeInTheDocument();
    expect(listMock).not.toHaveBeenCalled();

    resolveMine([{ org_login: "acme", role: "member" }]);
    expect(await screen.findByText(/limited to organization admins/)).toBeInTheDocument();
    expect(listMock).not.toHaveBeenCalled();
  });

  it("does not guess when the role lookup fails: the admin UI shows and the API decides", async () => {
    mineMock.mockRejectedValue(new Error("memberships unavailable"));
    renderPage();

    expect(await screen.findByRole("button", { name: /send invite/i })).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledWith("acme");
  });

  it("shows an error with Retry when invitations fail to load, not 'No invitations yet'", async () => {
    mineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    listMock.mockRejectedValueOnce(new Error("Org admin access required"));
    renderPage();

    expect(await screen.findByText(/Couldn't load invitations: Org admin access required/)).toBeInTheDocument();
    expect(screen.queryByText("No invitations yet")).not.toBeInTheDocument();

    listMock.mockResolvedValue([
      { id: 1, org_id: 1, email: "a@example.com", status: "pending", created_at: "", accepted_at: null },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("a@example.com")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load invitations/)).not.toBeInTheDocument();
  });
});
