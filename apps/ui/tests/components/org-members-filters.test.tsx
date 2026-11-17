import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

let overlay = true;
const membersMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: { list: vi.fn().mockResolvedValue([]), revoke: vi.fn(), create: vi.fn() },
    collab: {
      members: (...a: unknown[]) => membersMock(...a),
      outsideCollaborators: vi.fn(),
      invitations: vi.fn().mockResolvedValue({ org: "acme", invitations: [] }),
      permissionAudit: vi.fn(),
      inactiveMembers: vi.fn(),
    },
    tokens: { resolve: vi.fn().mockRejectedValue(new Error("no saved token")) },
    orgs: { mine: () => Promise.resolve([{ org_login: "acme", role: "admin" }]) },
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";

const member = (login: string, two_factor_enabled: boolean | null) => ({
  login, avatar_url: "", role: "member", site_admin: false, two_factor_enabled,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OrgMembersPage />
    </QueryClientProvider>,
  );
}

describe("org members roster filters (#1128)", () => {
  beforeEach(() => {
    overlay = true;
    membersMock.mockReset();
    membersMock.mockImplementation(async () => ({
      org: "acme",
      two_factor_overlay_available: overlay,
      members: [member("alice", false), member("bob", true), member("carol", null)],
    }));
  });
  afterEach(() => {
    cleanup();
    window.history.replaceState(null, "", "/");
  });

  it("shows only members whose 2FA is off, and keeps the choice in the query string", async () => {
    renderPage();
    await screen.findByText("alice");
    expect(screen.getByText("bob")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("2FA off"));
    expect(screen.queryByText("bob")).not.toBeInTheDocument();
    expect(screen.queryByText("carol")).not.toBeInTheDocument(); // unknown is not "off"
    expect(screen.getByText("alice")).toBeInTheDocument();
    await waitFor(() => expect(window.location.search).toBe("?no2fa=1"));
  });

  it("restores the search, role and 2FA filters from the query string", async () => {
    window.history.replaceState(null, "", "/?q=ali&role=admin&no2fa=1");
    renderPage();

    await waitFor(() => expect(screen.getByLabelText("Filter by role")).toHaveValue("admin"));
    expect(screen.getByLabelText("Search members by login")).toHaveValue("ali");
    expect(screen.getByLabelText("2FA off")).toBeChecked();
    await waitFor(() => expect(membersMock).toHaveBeenCalledWith("acme", "admin", undefined));
  });

  it("disables the 2FA filter when the 2FA overlay has not run", async () => {
    overlay = false;
    renderPage();
    await screen.findByText("alice");
    expect(screen.getByLabelText("2FA off")).toBeDisabled();
  });
});
