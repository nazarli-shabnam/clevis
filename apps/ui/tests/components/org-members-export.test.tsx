import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const downloadTextFileMock = vi.fn();
vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));

const membersMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: { list: vi.fn().mockResolvedValue([]), revoke: vi.fn(), create: vi.fn() },
    collab: {
      members: (...args: unknown[]) => membersMock(...args),
      outsideCollaborators: vi.fn(),
      invitations: vi.fn().mockResolvedValue({ org: "acme", invitations: [] }),
      permissionAudit: vi.fn(),
      inactiveMembers: vi.fn(),
      membership: vi.fn(),
    },
    tokens: { resolve: vi.fn().mockRejectedValue(new Error("no saved token")) },
    orgs: { mine: vi.fn().mockResolvedValue([{ org_login: "acme", role: "admin" }]) },
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrgMembersPage />
    </QueryClientProvider>,
  );
}

describe("OrgMembersPage CSV export", () => {
  beforeEach(() => {
    downloadTextFileMock.mockReset();
    membersMock.mockReset();
    membersMock.mockResolvedValue({
      org: "acme",
      two_factor_overlay_available: true,
      members: [
        { login: "alice", avatar_url: "", role: "admin", site_admin: false, two_factor_enabled: true },
        { login: "bob", avatar_url: "", role: "member", site_admin: false, two_factor_enabled: false },
        { login: "carol", avatar_url: "", role: "member", site_admin: false, two_factor_enabled: null },
      ],
    });
  });

  afterEach(() => cleanup());

  it("exports the roster with an explicit 'unknown' for members whose 2FA status isn't known", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /export csv/i }));

    const [filename, csv, mime] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-members-acme-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(mime).toBe("text/csv");
    expect(csv.split("\r\n")).toEqual(["Member,Role,2FA", "alice,admin,yes", "bob,member,no", "carol,member,unknown"]);
  });

  it("exports only the members that match the filter", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("carol")).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText("Search by login…"), { target: { value: "bo" } });
    await waitFor(() => expect(screen.queryByText("alice")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    expect(downloadTextFileMock.mock.calls[0][1].split("\r\n")).toEqual(["Member,Role,2FA", "bob,member,no"]);
  });
});
