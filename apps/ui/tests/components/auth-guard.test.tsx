import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let mockPathname = "/repos";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => mockPathname,
}));

import { AuthGuard } from "@/components/auth-guard";
import { AuthProvider, useAuth } from "@/lib/auth-context";

const TOKEN_KEY = "clevis:token";

function b64url(value: object): string {
  return btoa(JSON.stringify(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function makeJwt(sub: number, email: string): string {
  const header = b64url({ alg: "none", typ: "JWT" });
  const payload = b64url({
    sub: String(sub),
    email,
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

describe("AuthGuard clevis:unauthorized handling", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    mockPathname = "/repos";
    localStorage.setItem(TOKEN_KEY, makeJwt(1, "user@example.com"));

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return Promise.resolve(
            jsonResponse({ id: 1, email: "user@example.com", name: null, is_workspace_admin: false }),
          );
        }
        if (url.endsWith("/auth/logout")) {
          return Promise.resolve(new Response(null, { status: 204 }));
        }
        if (url.endsWith("/auth/setup-required")) {
          return Promise.resolve(jsonResponse({ setup_required: false }));
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

  it("calls logout() (clearing the token) and redirects to /login on a clevis:unauthorized event", async () => {
    render(
      <AuthProvider>
        <AuthGuard>
          <div>protected content</div>
        </AuthGuard>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("protected content")).toBeInTheDocument();
    });

    await act(async () => {
      window.dispatchEvent(new Event("clevis:unauthorized"));
    });

    // logout() clears the stored token synchronously — proves logout() ran, not just a redirect.
    await waitFor(() => {
      expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    });

    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("does not log out or redirect on a clevis:unauthorized event while on a public route", async () => {
    // e.g. a stale token from a previous session sitting in localStorage, attached to a
    // best-effort call (like an invite preview) that happens to 401 -- must not force-log-out
    // someone merely viewing a public page.
    mockPathname = "/invite/abc123";

    render(
      <AuthProvider>
        <AuthGuard>
          <div>invite preview</div>
        </AuthGuard>
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("invite preview")).toBeInTheDocument();
    });

    await act(async () => {
      window.dispatchEvent(new Event("clevis:unauthorized"));
    });

    // Give any (incorrect) async logout/redirect a chance to have fired before asserting
    // it didn't -- avoids a false-pass from asserting too early.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(localStorage.getItem(TOKEN_KEY)).not.toBeNull();
    expect(replace).not.toHaveBeenCalledWith("/login");
    expect(screen.getByText("invite preview")).toBeInTheDocument();
  });
});

describe("AuthGuard authUnconfirmed banner", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    mockPathname = "/repos";
    localStorage.setItem(TOKEN_KEY, makeJwt(1, "user@example.com"));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("shows a banner once the session can't be confirmed with the server, without blocking the page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.reject(new Error("network down"));
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      }),
    );

    render(
      <AuthProvider>
        <AuthGuard>
          <div>protected content</div>
        </AuthGuard>
      </AuthProvider>,
    );

    await waitFor(() => expect(screen.getByText("protected content")).toBeInTheDocument());
    expect(screen.queryByText(/couldn.t confirm your session/i)).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    await waitFor(() =>
      expect(screen.getByText(/couldn.t confirm your session/i)).toBeInTheDocument(),
    );
    // Still renders the page — the stale optimistic state is surfaced, not hidden behind it.
    expect(screen.getByText("protected content")).toBeInTheDocument();
  });
});

function SetSessionWithInvite() {
  const { setSession } = useAuth();
  return (
    <button
      onClick={() =>
        setSession(makeJwt(1, "user@example.com"), { id: 1, email: "user@example.com", name: null, is_workspace_admin: false }, [
          { org_login: "acme", expires_at: "2030-01-01T00:00:00Z" },
        ])
      }
    >
      trigger setSession
    </button>
  )
}

describe("AuthGuard pending invitations banner", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    mockPathname = "/repos";
    localStorage.setItem(TOKEN_KEY, makeJwt(1, "user@example.com"));

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

  it("shows an informational (non-link) notice naming the org and dismisses it", async () => {
    render(
      <AuthProvider>
        <AuthGuard>
          <SetSessionWithInvite />
        </AuthGuard>
      </AuthProvider>,
    );

    const triggerButton = await screen.findByRole("button", { name: /trigger setSession/i });
    await act(async () => {
      triggerButton.click();
    });

    const notice = await screen.findByText(/pending invite to join.*acme/i);
    // Must never be a link — no accept token is exposed to this banner (see
    // PendingInvitationSummary), since "logged in as email X" isn't proof of owning
    // inbox X in this app (no email verification on registration).
    expect(screen.queryByRole("link", { name: /acme/i })).not.toBeInTheDocument();

    await act(async () => {
      screen.getByRole("button", { name: /dismiss/i }).click();
    });

    expect(notice).not.toBeInTheDocument();
  });
});
