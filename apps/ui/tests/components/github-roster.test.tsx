import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSyncExternalStore } from "react";

// A minimal reactive store standing in for Next's router-driven searchParams so a
// tab click's router.replace(...) actually triggers a re-render in the test, the
// same way real navigation would (matches the pattern in security-page.test.tsx).
let mockSearchParams = new URLSearchParams();
const searchParamsListeners = new Set<() => void>();
const routerReplaceMock = vi.fn((url: string) => {
  mockSearchParams = new URLSearchParams(url.split("?")[1] ?? "");
  searchParamsListeners.forEach((listener) => listener());
});

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: routerReplaceMock }),
  useSearchParams: () =>
    useSyncExternalStore(
      (listener) => {
        searchParamsListeners.add(listener);
        return () => searchParamsListeners.delete(listener);
      },
      () => mockSearchParams,
    ),
}));

const membersMock = vi.fn();
const outsideMock = vi.fn();
const pendingInvitationsMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: {
      list: vi.fn().mockResolvedValue([]),
      revoke: vi.fn(),
      create: vi.fn(),
    },
    collab: {
      members: (...args: unknown[]) => membersMock(...args),
      outsideCollaborators: (...args: unknown[]) => outsideMock(...args),
      invitations: (...args: unknown[]) => pendingInvitationsMock(...args),
      membership: vi.fn(),
    },
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

describe("GithubRoster (Collaborators page)", () => {
  beforeEach(() => {
    membersMock.mockReset();
    outsideMock.mockReset();
    pendingInvitationsMock.mockReset();
    routerReplaceMock.mockClear();
    mockSearchParams = new URLSearchParams();
    outsideMock.mockResolvedValue({ org: "acme", collaborators: [], repos_scanned: 0, repos_total: 0 });
    pendingInvitationsMock.mockResolvedValue({ org: "acme", invitations: [] });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders members with role badges and 2FA chips, and the footer count", async () => {
    membersMock.mockResolvedValue({
      org: "acme",
      members: [
        { login: "alice", avatar_url: "", role: "admin", site_admin: false, two_factor_enabled: true },
        { login: "bob", avatar_url: "", role: "member", site_admin: false, two_factor_enabled: false },
      ],
      two_factor_overlay_available: true,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("alice")).toBeInTheDocument();
    });
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("✓ 2FA")).toBeInTheDocument();
    expect(screen.getByText("No 2FA")).toBeInTheDocument();
    expect(screen.getByText("Members without 2FA: 1")).toBeInTheDocument();
  });

  it("omits the 2FA chip and footer when the overlay is unavailable", async () => {
    membersMock.mockResolvedValue({
      org: "acme",
      members: [{ login: "alice", avatar_url: "", role: "member", site_admin: false, two_factor_enabled: null }],
      two_factor_overlay_available: false,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("alice")).toBeInTheDocument();
    });
    expect(screen.queryByText("✓ 2FA")).not.toBeInTheDocument();
    expect(screen.queryByText("No 2FA")).not.toBeInTheDocument();
    expect(screen.queryByText(/Members without 2FA/)).not.toBeInTheDocument();
  });

  it("only fetches the active tab's data, gating other tabs", async () => {
    membersMock.mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: true });

    renderPage();

    await waitFor(() => {
      expect(membersMock).toHaveBeenCalled();
    });
    expect(outsideMock).not.toHaveBeenCalled();
    expect(pendingInvitationsMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Outside" }));

    await waitFor(() => {
      expect(outsideMock).toHaveBeenCalled();
    });
  });

  it("shows the repo-scan cap note when outside collaborators are capped", async () => {
    membersMock.mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: true });
    outsideMock.mockResolvedValue({
      org: "acme",
      collaborators: [{ login: "carol", avatar_url: "", repos: ["acme/api"] }],
      repos_scanned: 50,
      repos_total: 140,
    });

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Outside" }));

    await waitFor(() => {
      expect(screen.getByText("Scanned 50 of 140 repos")).toBeInTheDocument();
    });
  });

  it("renders pending GitHub invitations distinctly from Clevis workspace invitations", async () => {
    membersMock.mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: true });
    pendingInvitationsMock.mockResolvedValue({
      org: "acme",
      invitations: [
        { login: "dave", email: null, role: "member", invited_at: "2026-07-01T00:00:00Z", inviter: "alice" },
      ],
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Clevis workspace invitations")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Pending GitHub invitations" }));

    await waitFor(() => {
      expect(screen.getByText("dave")).toBeInTheDocument();
    });
  });

  it("surfaces an error message when the members query fails", async () => {
    membersMock.mockRejectedValue(new Error("No GitHub App installation found"));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("No GitHub App installation found")).toBeInTheDocument();
    });
  });
});
