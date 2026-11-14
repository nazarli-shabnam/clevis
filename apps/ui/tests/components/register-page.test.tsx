import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
const registerMock = vi.fn();
const setupRequiredMock = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => searchParams,
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    auth: {
      register: (...args: unknown[]) => registerMock(...args),
      setupRequired: (...args: unknown[]) => setupRequiredMock(...args),
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
  it("replaces the form with a notice when registration is turned off (#902)", async () => {
    setupRequiredMock.mockResolvedValue({ setup_required: false, registration_enabled: false, github_oauth_enabled: true });
    renderPage();

    expect(await screen.findByText(/New sign-ups are turned off/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create account/i })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("keeps the form and sends the invitation token when sign-ups are closed but the user came from an invite (#1011)", async () => {
    setupRequiredMock.mockResolvedValue({ setup_required: false, registration_enabled: false, github_oauth_enabled: true });
    searchParams = new URLSearchParams({ next: "/invite/tok_123-abc" });
    registerMock.mockResolvedValue({
      access_token: "a.b.c",
      user: { id: 1, email: "new@example.com", name: null, is_workspace_admin: false },
      pending_invitations: [],
    });
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("you@example.com"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "supersecret1234" } });
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "supersecret1234" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));

    await waitFor(() => expect(registerMock).toHaveBeenCalledWith("new@example.com", "supersecret1234", undefined, "tok_123-abc"));
    expect(screen.queryByText(/New sign-ups are turned off/)).not.toBeInTheDocument();
  });

  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    registerMock.mockReset();
    setupRequiredMock.mockReset();
    setupRequiredMock.mockResolvedValue({ setup_required: false, registration_enabled: true, github_oauth_enabled: true });
    searchParams = new URLSearchParams();

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

  it("renders inside a main landmark (public routes get no shell <main>)", () => {
    renderPage();

    expect(screen.getByRole("main")).toBeInTheDocument();
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

  async function submitRegistration() {
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "new@example.com" } });
    fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "supersecret1234" } });
    fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "supersecret1234" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
  }

  it("returns to the ?next= destination after registering, and links back to login with it (#661)", async () => {
    searchParams = new URLSearchParams({ next: "/invite/abc123" });
    registerMock.mockResolvedValue({
      access_token: "a.b.c",
      user: { id: 1, email: "new@example.com", name: null, is_workspace_admin: false },
      pending_invitations: [],
    });

    renderPage();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login?next=%2Finvite%2Fabc123");
    await submitRegistration();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/invite/abc123"));
  });

  it("ignores an off-site ?next= and goes home (#661)", async () => {
    searchParams = new URLSearchParams({ next: "//evil.example" });
    registerMock.mockResolvedValue({
      access_token: "a.b.c",
      user: { id: 1, email: "new@example.com", name: null, is_workspace_admin: false },
      pending_invitations: [],
    });

    renderPage();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    await submitRegistration();

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
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
