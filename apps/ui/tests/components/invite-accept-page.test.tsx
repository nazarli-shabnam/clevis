import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ token: "abc123" }),
  useRouter: () => ({ push }),
}));

const previewMock = vi.fn();
const acceptMock = vi.fn();
const resendVerificationMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: {
      preview: (...args: unknown[]) => previewMock(...args),
      accept: (...args: unknown[]) => acceptMock(...args),
    },
    auth: {
      resendVerification: (...args: unknown[]) => resendVerificationMock(...args),
    },
  },
}));

import InviteAcceptPage from "@/app/invite/[token]/page";
import { AuthProvider } from "@/lib/auth-context";

const TOKEN_KEY = "clevis:token";

function b64url(value: object): string {
  return btoa(JSON.stringify(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function makeJwt(): string {
  const header = b64url({ alg: "none", typ: "JWT" });
  const payload = b64url({
    sub: "1",
    email: "user@example.com",
    name: null,
    is_workspace_admin: false,
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  return `${header}.${payload}.sig`;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <InviteAcceptPage />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("InviteAcceptPage", () => {
  beforeEach(() => {
    localStorage.clear();
    push.mockClear();
    previewMock.mockReset();
    acceptMock.mockReset();
    resendVerificationMock.mockReset();
    localStorage.setItem(TOKEN_KEY, makeJwt());

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return Promise.resolve(
            jsonResponse({ id: 1, email: "user@example.com", name: null, is_workspace_admin: false }),
          );
        }
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a resend-verification button when accept fails because the email isn't verified", async () => {
    previewMock.mockResolvedValue({ org_login: "acme", status: "pending" });
    acceptMock.mockRejectedValue(new Error("Verify your email before accepting this invitation"));

    renderPage();

    const acceptButton = await screen.findByRole("button", { name: /accept invitation/i });
    fireEvent.click(acceptButton);

    const resendButton = await screen.findByRole("button", { name: /resend verification email/i });
    resendVerificationMock.mockResolvedValue({ ok: true, already_verified: false });
    fireEvent.click(resendButton);

    await waitFor(() => expect(resendVerificationMock).toHaveBeenCalled());
    await screen.findByText(/verification email sent/i);
  });

  it("does not show a resend button for an unrelated accept failure", async () => {
    previewMock.mockResolvedValue({ org_login: "acme", status: "pending" });
    acceptMock.mockRejectedValue(new Error("Invitation has expired"));

    renderPage();

    const acceptButton = await screen.findByRole("button", { name: /accept invitation/i });
    fireEvent.click(acceptButton);

    await screen.findByText(/invitation has expired/i);
    expect(screen.queryByRole("button", { name: /resend verification email/i })).not.toBeInTheDocument();
  });

  it("redirects home after a successful accept", async () => {
    previewMock.mockResolvedValue({ org_login: "acme", status: "pending" });
    acceptMock.mockResolvedValue({ org_login: "acme", role: "member" });

    renderPage();

    const acceptButton = await screen.findByRole("button", { name: /accept invitation/i });
    fireEvent.click(acceptButton);

    await screen.findByText(/joined acme/i);
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  });
});
