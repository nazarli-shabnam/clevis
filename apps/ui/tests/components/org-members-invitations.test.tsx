import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const listMock = vi.fn();
const createMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: {
      list: (...args: unknown[]) => listMock(...args),
      create: (...args: unknown[]) => createMock(...args),
      revoke: vi.fn(),
    },
    collab: {
      members: vi.fn().mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: false }),
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

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const inFuture = (ms: number) => new Date(Date.now() + ms).toISOString();

function invitation(id: number, email: string, status: string, expires_at: string) {
  return { id, org_id: 1, email, status, created_at: "2026-09-01T00:00:00Z", accepted_at: null, expires_at };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrgMembersPage />
    </QueryClientProvider>,
  );
}

describe("OrgMembersPage invitations", () => {
  beforeEach(() => {
    listMock.mockReset();
    createMock.mockReset();
  });
  afterEach(() => cleanup());

  it("shows when each invitation expires, with a warning for soon and for already expired", async () => {
    listMock.mockResolvedValue([
      // fixtures sit in the middle of their bucket so a slow run can't tip them over a boundary
      invitation(1, "week@example.com", "pending", inFuture(6 * DAY + 12 * HOUR)),
      invitation(2, "soon@example.com", "pending", inFuture(5 * HOUR + 30 * 60_000)),
      invitation(3, "late@example.com", "expired", inFuture(-3 * DAY)),
      invitation(4, "done@example.com", "accepted", inFuture(-DAY)),
      invitation(5, "stale@example.com", "pending", inFuture(-2 * HOUR)),
    ]);
    renderPage();

    const rowOf = async (email: string) => (await screen.findByText(email)).closest("tr")!;
    expect(await screen.findByRole("columnheader", { name: "Expires" })).toBeInTheDocument();

    expect(within(await rowOf("week@example.com")).getByText("in 7 days")).toBeInTheDocument();
    const soonRow = await rowOf("soon@example.com");
    const soon = within(soonRow).getByText(/in 6 hours/);
    expect(soon.closest("td")).toHaveClass("text-yellow-400");
    expect(within(soonRow).getByText("(expiring soon)")).toBeInTheDocument(); // not colour alone
    const late = within(await rowOf("late@example.com")).getByText(/^expired 3 days ago$/);
    expect(late).toHaveClass("text-destructive");
    expect(within(await rowOf("done@example.com")).getByText("—")).toBeInTheDocument();

    // A pending invitation whose time has passed must not read "pending" next to "expired",
    // nor offer to revoke it.
    const stale = await rowOf("stale@example.com");
    expect(within(stale).getByText("expired")).toBeInTheDocument();
    expect(within(stale).getByText(/^expired 2 hours ago$/)).toBeInTheDocument();
    expect(within(stale).queryByRole("button", { name: /revoke/i })).toBeNull();
    expect(within(await rowOf("week@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
  });

  it("lets the admin copy the one-time invite link after creating an invitation", async () => {
    listMock.mockResolvedValue([]);
    createMock.mockResolvedValue({
      invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_abc",
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: "new@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /send invite/i }));

    expect(await screen.findByText("https://clevis.example/invite/tok_abc")).toBeInTheDocument();
    expect(screen.getByText(/shown only now/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy invitation link" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("https://clevis.example/invite/tok_abc"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();

    // a second invitation brings a new link and a fresh button: it must not still say "Copied"
    createMock.mockResolvedValue({
      invitation: invitation(10, "next@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_def",
    });
    fireEvent.change(screen.getByPlaceholderText("teammate@example.com"), { target: { value: "next@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /send invite/i }));

    expect(await screen.findByText("https://clevis.example/invite/tok_def")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy invitation link" })).toHaveTextContent(/^Copy$/);
  });
});
