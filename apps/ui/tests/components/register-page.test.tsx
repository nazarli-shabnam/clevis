import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
const registerMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    auth: {
      register: (...args: unknown[]) => registerMock(...args),
    },
  },
}));

import RegisterPage from "@/app/register/page";
import { AuthProvider, useAuth } from "@/lib/auth-context";

function PendingInvitationsPeek() {
  const { pendingInvitations } = useAuth();
  return (
    <ul>
      {pendingInvitations.map((inv) => (
        <li key={inv.org_login}>{inv.org_login}</li>
      ))}
    </ul>
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <RegisterPage />
      <PendingInvitationsPeek />
    </AuthProvider>,
  );
}

describe("RegisterPage", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    registerMock.mockReset();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.resolve(new Response(null, { status: 401 }));
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("carries pending_invitations from the register response into the auth context", async () => {
    registerMock.mockResolvedValue({
      access_token: "a.b.c",
      user: { id: 1, email: "new@example.com", name: null, is_workspace_admin: false },
      pending_invitations: [{ org_login: "acme", expires_at: "2030-01-01T00:00:00Z" }],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "supersecret1234" } });
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "supersecret1234" } });

    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(screen.getByText("acme")).toBeInTheDocument());
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("registering with no pending invitations leaves the list empty", async () => {
    registerMock.mockResolvedValue({
      access_token: "a.b.c",
      user: { id: 1, email: "new@example.com", name: null, is_workspace_admin: false },
      pending_invitations: [],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "supersecret1234" } });
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "supersecret1234" } });

    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });
});
