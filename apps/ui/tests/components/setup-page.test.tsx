import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

import SetupPage from "@/app/setup/page";
import { AuthProvider } from "@/lib/auth-context";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// First /auth/setup-required call is the mount check; later ones are the re-check after a failed submit.
function stubFetch(recheckSetupRequired: boolean | "unreachable") {
  let setupRequiredCalls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return Promise.resolve(new Response(null, { status: 401 }));
      if (url.endsWith("/auth/setup-required")) {
        setupRequiredCalls += 1;
        if (setupRequiredCalls === 1) return Promise.resolve(jsonResponse({ setup_required: true }));
        if (recheckSetupRequired === "unreachable") return Promise.reject(new Error("network down"));
        return Promise.resolve(jsonResponse({ setup_required: recheckSetupRequired }));
      }
      if (url.endsWith("/auth/setup")) return Promise.resolve(jsonResponse({ detail: "Setup already complete" }, 409));
      return Promise.reject(new Error(`Unexpected fetch: ${url}`));
    }),
  );
}

async function submitForm() {
  render(
    <AuthProvider>
      <SetupPage />
    </AuthProvider>,
  );
  fireEvent.change(await screen.findByPlaceholderText("you@example.com"), { target: { value: "a@b.co" } });
  fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: "a-long-password-1" } });
  fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: "a-long-password-1" } });
  fireEvent.click(screen.getByRole("button", { name: /create account/i }));
}

describe("SetupPage first-run race", () => {
  beforeEach(() => {
    localStorage.clear();
    replace.mockClear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("sends the loser of the race to /login once setup is no longer required", async () => {
    stubFetch(false);
    await submitForm();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?error=setup_complete"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps the form and shows the error when setup is still required", async () => {
    stubFetch(true);
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent("Setup already complete");
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps the form and shows the error when the re-check itself fails", async () => {
    stubFetch("unreachable");
    await submitForm();
    expect(await screen.findByRole("alert")).toHaveTextContent("Setup already complete");
    expect(replace).not.toHaveBeenCalled();
  });
});
