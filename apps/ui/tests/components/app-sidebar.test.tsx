import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/",
}));

import { AppSidebar } from "@/components/app-sidebar";
import { AuthProvider } from "@/lib/auth-context";
import { SidebarProvider } from "@/components/ui/sidebar";

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
    name: "User",
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

function renderSidebar() {
  return render(
    <AuthProvider>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </AuthProvider>,
  );
}

describe("AppSidebar Invite members button", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    localStorage.setItem(TOKEN_KEY, makeJwt());

    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) {
          return Promise.resolve(
            jsonResponse({ id: 1, email: "user@example.com", name: "User", is_workspace_admin: false }),
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

  it("links to the org's members page when a default_org is set", async () => {
    localStorage.setItem("default_org", "acme");
    renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: /user/i }));

    const link = await screen.findByRole("link", { name: /invite members/i });
    expect(link).toHaveAttribute("href", "/settings/org/acme/members");
  });

  it("links to Settings when no default_org is set, instead of being a dead disabled button", async () => {
    renderSidebar();

    fireEvent.click(screen.getByRole("button", { name: /guest|user/i }));

    const link = await screen.findByRole("link", { name: /invite members/i });
    expect(link).toHaveAttribute("href", "/settings");
    expect(link).not.toHaveAttribute("disabled");
  });
});
