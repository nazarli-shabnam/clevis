import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/repos",
}));

import { AuthGuard } from "@/components/auth-guard";
import { AuthProvider } from "@/lib/auth-context";

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
});
