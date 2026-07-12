import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
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
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
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

    // Row being revoked is disabled; the other row stays interactive.
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
});
