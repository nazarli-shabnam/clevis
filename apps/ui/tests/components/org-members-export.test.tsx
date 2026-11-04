import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(mockRoster ? `roster=${mockRoster}` : ""),
}));
let mockRoster = "";

const downloadTextFileMock = vi.fn();
vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));

const membersMock = vi.fn();
const outsideMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: { list: vi.fn().mockResolvedValue([]), revoke: vi.fn(), create: vi.fn() },
    collab: {
      members: (...args: unknown[]) => membersMock(...args),
      outsideCollaborators: (...args: unknown[]) => outsideMock(...args),
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
    mockRoster = "";
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

  it("exports the outside collaborators with their repos, noting a partial scan", async () => {
    outsideMock.mockResolvedValue({
      org: "acme",
      repos_scanned: 40,
      repos_total: 55,
      collaborators: [
        { login: "dana", avatar_url: "", repos: ["api", "web"] },
        { login: "=evil", avatar_url: "", repos: ["api"] },
      ],
    });
    mockRoster = "outside";
    renderPage();
    expect(await screen.findByText("dana")).toBeInTheDocument();
    expect(screen.getByText("Covers 40 of 55 repos")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /export csv/i }));

    const [filename, csv] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-outside-collaborators-acme-\d{4}-\d{2}-\d{2}\.csv$/);
    const lines = csv.split(String.fromCharCode(13, 10));
    expect(lines[0]).toBe("Collaborator,Repos,Repo count");
    expect(lines[1]).toBe("dana,api; web,2");
    expect(lines[2]).not.toMatch(/^=/); // formula-looking login is neutralised
  });
});

describe("OrgMembersPage roster paging", () => {
  afterEach(() => {
    vi.useRealTimers();
    cleanup();
  });

  it("stays on the page the admin is reviewing when the page re-renders on its own clock tick", async () => {
    mockRoster = "";
    membersMock.mockReset();
    membersMock.mockResolvedValue({
      org: "acme",
      two_factor_overlay_available: true,
      members: Array.from({ length: 25 }, (_, i) => ({
        login: `user${String(i).padStart(2, "0")}`,
        avatar_url: "",
        role: "member",
        site_admin: false,
        two_factor_enabled: true,
      })),
    });
    // Only the page's 60 s clock interval is faked; react-query and waitFor keep real timers.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    renderPage();

    await waitFor(() => expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(screen.getByText(/Page 2 of 2/)).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(screen.getByText(/Page 2 of 2/)).toBeInTheDocument();
  });
});
