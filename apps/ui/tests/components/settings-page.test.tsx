import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const orgsMineMock = vi.fn();
const installationsListMock = vi.fn();
const tokensListMock = vi.fn();
const configGetAllMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: (...args: unknown[]) => orgsMineMock(...args) },
    installations: { list: (...args: unknown[]) => installationsListMock(...args) },
    tokens: { list: (...args: unknown[]) => tokensListMock(...args) },
    config: { getAll: (...args: unknown[]) => configGetAllMock(...args) },
    auth: { patchMe: vi.fn() },
  },
}));

import { AuthProvider } from "@/lib/auth-context";
import SettingsPage from "@/app/settings/page";

const TOKEN_KEY = "clevis:token";

function b64url(value: object): string {
  return btoa(JSON.stringify(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function makeAdminJwt(): string {
  const header = b64url({ alg: "none", typ: "JWT" });
  const payload = b64url({
    sub: "1",
    email: "admin@example.com",
    name: "Admin",
    is_workspace_admin: true,
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  return `${header}.${payload}.`;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <SettingsPage />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("SettingsPage", () => {
  beforeEach(() => {
    orgsMineMock.mockReset();
    installationsListMock.mockReset();
    tokensListMock.mockReset();
    configGetAllMock.mockReset();
    localStorage.clear();
    localStorage.setItem(TOKEN_KEY, makeAdminJwt());
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the profile section and surfaces a retry when a section errors, and shows instance config for admins", async () => {
    orgsMineMock.mockRejectedValue(new Error("Failed to load organizations."));
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    expect(screen.getByRole("button", { name: "Save profile" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Failed to load organizations.")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Instance configuration")).toBeInTheDocument();
    });
    expect(screen.getAllByRole("button", { name: "Save" }).length).toBeGreaterThan(0);
  });
});
