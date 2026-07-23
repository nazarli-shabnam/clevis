import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => searchParams,
}));

import LoginPage from "@/app/login/page";
import { AuthProvider } from "@/lib/auth-context";

function renderPage() {
  return render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>,
  );
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("LoginPage GitHub OAuth button", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
    searchParams = new URLSearchParams();

    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/me")) return Promise.resolve(new Response(null, { status: 401 }));
        if (url.endsWith("/auth/setup-required")) return Promise.resolve(jsonResponse({ setup_required: false }));
        return Promise.reject(new Error(`Unexpected fetch: ${url}`));
      }),
    );

    // jsdom throws "Not implemented: navigation" if window.location.href is actually
    // assigned to a non-blank-page URL -- redefine it as a plain writable object so the
    // button's onClick can be exercised without a real navigation attempt.
    Object.defineProperty(window, "location", {
      writable: true,
      value: { href: "" },
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("appends the current next param to the GitHub OAuth login URL", async () => {
    searchParams = new URLSearchParams({ next: "/invite/abc123" });
    renderPage();

    const button = await screen.findByRole("button", { name: /sign in with github/i });
    fireEvent.click(button);

    await waitFor(() =>
      expect(window.location.href).toBe("http://localhost:8080/auth/github/login?next=%2Finvite%2Fabc123"),
    );
  });

  it("omits the next param when there is none (default '/')", async () => {
    renderPage();

    const button = await screen.findByRole("button", { name: /sign in with github/i });
    fireEvent.click(button);

    await waitFor(() => expect(window.location.href).toBe("http://localhost:8080/auth/github/login"));
  });
});
