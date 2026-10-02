import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const listMock = vi.fn();
const revokeMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: {
      list: (...args: unknown[]) => listMock(...args),
      revoke: (...args: unknown[]) => revokeMock(...args),
      create: vi.fn(),
    },
    collab: {
      members: vi.fn().mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: true }),
      outsideCollaborators: vi.fn().mockResolvedValue({ org: "acme", collaborators: [], repos_scanned: 0, repos_total: 0 }),
      invitations: vi.fn().mockResolvedValue({ org: "acme", invitations: [] }),
      membership: vi.fn(),
    },
    tokens: {
      resolve: vi.fn().mockRejectedValue(new Error("no saved token")),
    },
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

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

describe("OrgMembersPage per-row revoke pending state", () => {
  const invitations = [
    { id: 1, org_id: 1, email: "a@example.com", status: "pending" as const, created_at: "", accepted_at: null },
    { id: 2, org_id: 1, email: "b@example.com", status: "pending" as const, created_at: "", accepted_at: null },
  ];

  beforeEach(() => {
    listMock.mockReset();
    revokeMock.mockReset();
    listMock.mockResolvedValue(invitations);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("only disables the row being revoked, not every row", async () => {
    const revokeGate = deferred<void>();
    revokeMock.mockReturnValue(revokeGate.promise);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("a@example.com")).toBeInTheDocument();
    });

    const rows = screen.getAllByRole("button", { name: /revoke/i });
    expect(rows).toHaveLength(2);
    const [firstRevoke, secondRevoke] = rows;

    expect(firstRevoke).not.toBeDisabled();
    expect(secondRevoke).not.toBeDisabled();

    await act(async () => {
      firstRevoke.click();
    });

    expect(firstRevoke).toBeDisabled();
    expect(secondRevoke).not.toBeDisabled();

    await act(async () => {
      revokeGate.resolve();
      await revokeGate.promise;
    });

    await waitFor(() => {
      expect(firstRevoke).not.toBeDisabled();
    });
  });

  it("attributes a failure to its own invitation when a second revoke started later", async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    revokeMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("a@example.com")).toBeInTheDocument();
    });

    const [firstRevoke, secondRevoke] = screen.getAllByRole("button", { name: /revoke/i });

    await act(async () => {
      firstRevoke.click();
    });
    await act(async () => {
      secondRevoke.click();
    });
    expect(firstRevoke).toBeDisabled();
    expect(secondRevoke).toBeDisabled();

    await act(async () => {
      first.reject(new Error("Invitation already accepted"));
      await first.promise.catch(() => {});
    });

    const firstRow = screen.getByText("a@example.com").closest("tr")!;
    const secondRow = screen.getByText("b@example.com").closest("tr")!;
    await waitFor(() => {
      expect(firstRow).toHaveTextContent("Invitation already accepted");
    });
    expect(firstRevoke).not.toBeDisabled();
    expect(secondRevoke).toBeDisabled();
    expect(secondRow).not.toHaveTextContent("Invitation already accepted");
  });

  it("shows the error when revoking an invitation fails", async () => {
    revokeMock.mockRejectedValue(new Error("Invitation already accepted"));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("a@example.com")).toBeInTheDocument();
    });

    await act(async () => {
      screen.getAllByRole("button", { name: /revoke/i })[0].click();
    });

    await waitFor(() => {
      expect(screen.getByText("Invitation already accepted")).toBeInTheDocument();
    });
  });
});
