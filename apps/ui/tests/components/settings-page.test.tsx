import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const orgsMineMock = vi.fn();
const installationsListMock = vi.fn();
const tokensListMock = vi.fn();
const configGetAllMock = vi.fn();
const patchMeMock = vi.fn();
const configUpdateMock = vi.fn();
const routerReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: (...args: unknown[]) => orgsMineMock(...args) },
    installations: { list: (...args: unknown[]) => installationsListMock(...args) },
    tokens: { list: (...args: unknown[]) => tokensListMock(...args) },
    config: {
      getAll: (...args: unknown[]) => configGetAllMock(...args),
      update: (...args: unknown[]) => configUpdateMock(...args),
    },
    auth: { patchMe: (...args: unknown[]) => patchMeMock(...args) },
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

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
    patchMeMock.mockReset();
    configUpdateMock.mockReset();
    localStorage.clear();
    localStorage.setItem(TOKEN_KEY, makeAdminJwt());
  });

  afterEach(() => {
    cleanup();
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

  it("shows a saving spinner then a saved confirmation when the profile is updated", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    const patchGate = deferred<{ id: number; email: string; name: string | null; is_workspace_admin: boolean }>();
    patchMeMock.mockReturnValue(patchGate.promise);

    renderPage();

    const nameInput = screen.getByPlaceholderText("Your name");
    fireEvent.change(nameInput, { target: { value: "New Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Saving…/ })).toBeInTheDocument();
    });

    await act(async () => {
      patchGate.resolve({ id: 1, email: "admin@example.com", name: "New Name", is_workspace_admin: true });
      await patchGate.promise;
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Saved/ })).toBeInTheDocument();
    });
  });

  it("shows a saving spinner on the instance config field being saved", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    const updateGate = deferred<Record<string, string>>();
    configUpdateMock.mockReturnValue(updateGate.promise);

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Instance configuration")).toBeInTheDocument();
    });

    const saveButtons = screen.getAllByRole("button", { name: "Save" });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(saveButtons[0]).toBeDisabled();
    });

    await act(async () => {
      updateGate.resolve({ worker_poll_seconds: "5", registration_enabled: "true" });
      await updateGate.promise;
    });
  });
});
