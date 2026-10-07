import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const orgsMineMock = vi.fn();
const installationsListMock = vi.fn();
const installationsListForOrgMock = vi.fn();
const installationsRemoveMock = vi.fn();
const tokensListMock = vi.fn();
const tokensUpsertMock = vi.fn();
const tokensDeleteMock = vi.fn();
const configGetAllMock = vi.fn();
const patchMeMock = vi.fn();
const revokeSessionsMock = vi.fn();
const configUpdateMock = vi.fn();
const routerReplace = vi.fn();
let searchParams = new URLSearchParams();

// AuthProvider calls the real global `fetch` for /auth/me; unmocked, each failure waits a real
// 2s before retrying, making `waitFor` timing-dependent. Stub it to resolve immediately.
const fetchMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplace }),
  useSearchParams: () => searchParams,
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: (...args: unknown[]) => orgsMineMock(...args) },
    installations: {
      list: (...args: unknown[]) => installationsListMock(...args),
      listForOrg: (...args: unknown[]) => installationsListForOrgMock(...args),
      remove: (...args: unknown[]) => installationsRemoveMock(...args),
    },
    tokens: {
      list: (...args: unknown[]) => tokensListMock(...args),
      upsert: (...args: unknown[]) => tokensUpsertMock(...args),
      delete: (...args: unknown[]) => tokensDeleteMock(...args),
    },
    config: {
      getAll: (...args: unknown[]) => configGetAllMock(...args),
      update: (...args: unknown[]) => configUpdateMock(...args),
    },
    auth: {
      patchMe: (...args: unknown[]) => patchMeMock(...args),
      revokeSessions: (...args: unknown[]) => revokeSessionsMock(...args),
    },
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
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
    installationsListForOrgMock.mockReset();
    installationsRemoveMock.mockReset();
    tokensListMock.mockReset();
    tokensUpsertMock.mockReset();
    tokensDeleteMock.mockReset();
    configGetAllMock.mockReset();
    patchMeMock.mockReset();
    revokeSessionsMock.mockReset();
    configUpdateMock.mockReset();
    routerReplace.mockClear();
    searchParams = new URLSearchParams();
    localStorage.clear();
    localStorage.setItem(TOKEN_KEY, makeAdminJwt());
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ id: 1, email: "admin@example.com", name: "Admin", is_workspace_admin: true }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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
    // Two cards read the same failed "my-orgs" query, so each surfaces its own retry.
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThanOrEqual(1);

    await waitFor(() => {
      expect(screen.getByText("Instance configuration")).toBeInTheDocument();
    });
    expect(screen.getAllByRole("button", { name: "Save" }).length).toBeGreaterThan(0);
  });

  it("hides the saved tokens section from a non-admin, who would only see a permanent 403 (#660)", async () => {
    // A member: confirmed by /auth/me and by the stored JWT.
    const payload = b64url({ sub: "2", email: "member@example.com", name: "Member", is_workspace_admin: false, exp: Math.floor(Date.now() / 1000) + 3600 });
    localStorage.setItem(TOKEN_KEY, `${b64url({ alg: "none", typ: "JWT" })}.${payload}.`);
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ id: 2, email: "member@example.com", name: "Member", is_workspace_admin: false }), { status: 200 }),
    );
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockRejectedValue(new Error("Workspace admin access required"));

    renderPage();

    await screen.findByRole("button", { name: "Save profile" });
    expect(screen.queryByText("Personal access tokens (legacy)")).not.toBeInTheDocument();
    expect(screen.queryByText("Workspace admin access required")).not.toBeInTheDocument();
    expect(tokensListMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Instance configuration")).not.toBeInTheDocument();
  });

  it("lets a plain member open the roster from their org row ('View members'), admins get 'Manage members' (#663)", async () => {
    orgsMineMock.mockResolvedValue([
      { org_login: "acme", role: "admin" },
      { org_login: "widgets", role: "member" },
    ]);
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    expect(await screen.findByRole("link", { name: "Manage members" })).toHaveAttribute("href", "/settings/org/acme/members");
    expect(screen.getByRole("link", { name: "View members" })).toHaveAttribute("href", "/settings/org/widgets/members");
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

  it("saves a chosen leadership-digest cadence", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({
      worker_poll_seconds: "5",
      registration_enabled: "true",
      digest_cadence: "off",
    });
    configUpdateMock.mockResolvedValue({ digest_cadence: "weekly" });

    renderPage();

    const cadence = (await screen.findAllByDisplayValue("Off"))[0]; // Leadership Digest is listed before Scheduled Scans
    fireEvent.change(cadence, { target: { value: "weekly" } });

    const row = cadence.closest("div")!.parentElement!;
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(configUpdateMock).toHaveBeenCalledWith("digest_cadence", "weekly"));
  });

  it("saves the visible cadence value even when it was never in the server config", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    // digest_cadence absent from read_all() -> select shows "Off" but has no state entry.
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    configUpdateMock.mockResolvedValue({ digest_cadence: "off" });

    renderPage();

    const cadence = (await screen.findAllByDisplayValue("Off"))[0]; // Leadership Digest is listed before Scheduled Scans
    const row = cadence.closest("div")!.parentElement!;
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));

    // Must send "off", not "" (which _ENUM_KEYS would 422).
    await waitFor(() => expect(configUpdateMock).toHaveBeenCalledWith("digest_cadence", "off"));
  });

  it("renders a persisted non-default cadence and associates the label with the select", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({
      worker_poll_seconds: "5",
      registration_enabled: "true",
      digest_cadence: "monthly",
    });

    renderPage();

    // Label is tied to the control (htmlFor/id), so getByLabelText resolves it.
    const cadence = (await screen.findByLabelText("Leadership Digest")) as HTMLSelectElement;
    expect(cadence.tagName).toBe("SELECT");
    expect(cadence.value).toBe("monthly");
    expect(screen.getByDisplayValue("Monthly")).toBe(cadence);
  });

  it("renders no config fields when the config fails to load, and shows the real values after Retry", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock
      .mockRejectedValueOnce(new Error("config unavailable"))
      .mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "false" });

    renderPage();

    expect(await screen.findByText("config unavailable")).toBeInTheDocument();
    // No invented "Enabled" default to look at, and nothing for Save to overwrite the server with.
    expect(screen.queryByLabelText("Self-Registration")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: "Save" })).toHaveLength(0);
    expect(configUpdateMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    const registration = (await screen.findByLabelText("Self-Registration")) as HTMLSelectElement;
    expect(registration.value).toBe("false");
  });

  it("shows and saves the documented default for boolean keys that were never persisted", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    // Both booleans are absent from the (successfully loaded) config.
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5" });
    configUpdateMock.mockResolvedValue({});

    renderPage();

    const registration = (await screen.findByLabelText("Self-Registration")) as HTMLSelectElement;
    const hygiene = screen.getByLabelText("Score Hygiene Checks (default)") as HTMLSelectElement;
    expect(registration.value).toBe("true");
    expect(hygiene.value).toBe("false");

    fireEvent.click(within(registration.closest("div")!.parentElement!).getByRole("button", { name: "Save" }));
    // Must send the visible value, not "" (which the API rejects for a boolean key).
    await waitFor(() => expect(configUpdateMock).toHaveBeenCalledWith("registration_enabled", "true"));

    fireEvent.click(within(hygiene.closest("div")!.parentElement!).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(configUpdateMock).toHaveBeenCalledWith("score_hygiene_checks", "false"));
  });

  it("shows a success banner and strips the query param when landing with ?installed=1", async () => {
    searchParams = new URLSearchParams({ installed: "1" });
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("GitHub App installation connected.")).toBeInTheDocument();
    });
    expect(routerReplace).toHaveBeenCalledWith("/settings");
  });

  it("requires a second click to confirm revoking all sessions, then calls the endpoint", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    revokeSessionsMock.mockResolvedValue({ ok: true });

    renderPage();

    const revokeButton = await screen.findByRole("button", { name: /sign out of all devices/i });
    fireEvent.click(revokeButton);
    expect(revokeSessionsMock).not.toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: /click again to confirm/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /click again to confirm/i }));

    await waitFor(() => expect(revokeSessionsMock).toHaveBeenCalledTimes(1));
    // logout() clears the token, which removes the authenticated Settings page entirely.
    await waitFor(() => expect(localStorage.getItem(TOKEN_KEY)).toBeNull());
  });


  it("shows an unconfigured message instead of the install button when the App slug isn't set", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/GitHub App integration isn.t set up on this instance yet/i)).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /install github app/i })).not.toBeInTheDocument();
  });

  it("shows an empty state when nothing is connected", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No accounts connected yet/i)).toBeInTheDocument();
    });
  });

  it("shows a permission-drift notice under a connected account that's missing scopes", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockResolvedValue([
      {
        id: 2,
        account_login: "acme",
        account_type: "Organization",
        installation_id: 42,
        created_at: "2026-01-02T00:00:00Z",
        permissions_synced_at: "2026-09-01T00:00:00Z",
        blocked_features: [
          { feature: "stale_pr_nudges", label: "Stale pull-request nudges", missing: { pull_requests: "write" } },
        ],
      },
    ]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/needs extra GitHub access/i)).toBeInTheDocument();
      expect(screen.getByText("Stale pull-request nudges")).toBeInTheDocument();
    });
  });

  it("relabels the install button once an account is already connected", async () => {
    vi.stubEnv("NEXT_PUBLIC_GITHUB_APP_SLUG", "clevis");
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "shabnam", account_type: "User", installation_id: 7, created_at: "2026-01-01T00:00:00Z" },
    ]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    expect(await screen.findByRole("button", { name: /install on another account or org/i })).toBeInTheDocument();
    vi.unstubAllEnvs();
  });

  it("lists both personal and admin-org installations, and disconnects one after a confirm click", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "shabnam", account_type: "User", installation_id: 7, created_at: "2026-01-01T00:00:00Z" },
    ]);
    installationsListForOrgMock.mockResolvedValue([
      { id: 2, account_login: "acme", account_type: "Organization", installation_id: 42, created_at: "2026-01-02T00:00:00Z" },
    ]);
    installationsRemoveMock.mockResolvedValue(undefined);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    // "acme" also appears in the membership card, which resolves earlier; wait on the two
    // Disconnect buttons so the org-scoped installation row has settled.
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /disconnect/i })).toHaveLength(2);
    });
    expect(installationsListForOrgMock).toHaveBeenCalledWith("acme");

    const disconnectButtons = screen.getAllByRole("button", { name: /disconnect/i });

    fireEvent.click(disconnectButtons[0]);
    expect(installationsRemoveMock).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/shabnam/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: /^disconnect$/i }));

    await waitFor(() => {
      expect(installationsRemoveMock).toHaveBeenCalledWith({ scope: "me" }, 7);
    });
  });

  it("disconnects an org-scoped installation with the org scope, not the personal one", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockResolvedValue([
      { id: 2, account_login: "acme", account_type: "Organization", installation_id: 42, created_at: "2026-01-02T00:00:00Z" },
    ]);
    installationsRemoveMock.mockResolvedValue(undefined);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    const disconnectButton = await screen.findByRole("button", { name: /disconnect/i });
    fireEvent.click(disconnectButton);
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^disconnect$/i }));

    await waitFor(() => {
      expect(installationsRemoveMock).toHaveBeenCalledWith({ scope: "org", orgLogin: "acme" }, 42);
    });
  });

  it("does not fetch installations for orgs the caller is only a member of, not an admin", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/No accounts connected yet/i)).toBeInTheDocument();
    });
    expect(installationsListForOrgMock).not.toHaveBeenCalled();
  });

  it("retries all three connected-accounts queries when Retry is clicked", async () => {
    orgsMineMock.mockRejectedValue(new Error("boom"));
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Failed to load connected accounts.")).toBeInTheDocument();
    });

    orgsMineMock.mockClear();
    installationsListMock.mockClear();
    const retryButtons = screen.getAllByRole("button", { name: "Retry" });
    fireEvent.click(retryButtons[retryButtons.length - 1]);

    await waitFor(() => {
      expect(orgsMineMock).toHaveBeenCalled();
      expect(installationsListMock).toHaveBeenCalled();
    });
  });

  it("shows a spinner on the row being disconnected while the request is in flight", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "shabnam", account_type: "User", installation_id: 7, created_at: "2026-01-01T00:00:00Z" },
    ]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    const removeGate = deferred<void>();
    installationsRemoveMock.mockReturnValue(removeGate.promise);

    renderPage();

    const disconnectButton = await screen.findByRole("button", { name: /disconnect/i });
    fireEvent.click(disconnectButton);
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^disconnect$/i }));

    await waitFor(() => {
      expect(installationsRemoveMock).toHaveBeenCalled();
    });
    // Row button reverts to "Disconnect" while the dialog shows the busy state.
    expect(screen.queryByRole("button", { name: "Disconnect" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /working/i })).toBeDisabled();

    await act(async () => {
      removeGate.resolve();
      await removeGate.promise;
    });
  });

  it("shows an error message and lets the user try again when disconnect fails", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "shabnam", account_type: "User", installation_id: 7, created_at: "2026-01-01T00:00:00Z" },
    ]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    installationsRemoveMock.mockRejectedValue(new Error("GitHub API unreachable"));

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /disconnect/i }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^disconnect$/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("GitHub API unreachable");
    });
    // Row is still there and retryable; the dialog closed on error rather than sticking open.
    expect(screen.getByText("shabnam")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("closes the disconnect confirm dialog without disconnecting when Cancel is clicked", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "shabnam", account_type: "User", installation_id: 7, created_at: "2026-01-01T00:00:00Z" },
    ]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /disconnect/i }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(installationsRemoveMock).not.toHaveBeenCalled();
    expect(screen.getByText("shabnam")).toBeInTheDocument();
  });

  it("adds a saved token via the Add token form", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    tokensUpsertMock.mockResolvedValue({ org: "acme", label: "ci", created_at: "", updated_at: "" });

    renderPage();

    await waitFor(() => expect(tokensListMock).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Org or owner"), { target: { value: " acme " } });
    fireEvent.change(screen.getByPlaceholderText("ghp_… token"), { target: { value: " ghp_manual_token " } });
    fireEvent.change(screen.getByPlaceholderText("Label (optional)"), { target: { value: " ci " } });
    fireEvent.click(screen.getByRole("button", { name: /save token/i }));

    await waitFor(() =>
      expect(tokensUpsertMock).toHaveBeenCalledWith("acme", "ghp_manual_token", "ci"),
    );
  });

  it("shows the error and lets the user retry when saving the profile fails", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    patchMeMock
      .mockRejectedValueOnce(new Error("Name is too long"))
      .mockResolvedValue({ id: 1, email: "admin@example.com", name: "New Name", is_workspace_admin: true });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("Your name"), { target: { value: "New Name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText("Name is too long")).toBeInTheDocument();
    // No "Saved" confirmation for a failed save, and the button is usable again.
    expect(screen.queryByRole("button", { name: /Saved/ })).not.toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Save profile" });
    expect(retry).toBeEnabled();

    fireEvent.click(retry);
    expect(await screen.findByRole("button", { name: /Saved/ })).toBeInTheDocument();
    expect(screen.queryByText("Name is too long")).not.toBeInTheDocument();
  });

  it("clears a previous \"Saved\" as soon as the next profile save starts, so a failure isn't shown as saved", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    patchMeMock
      .mockResolvedValueOnce({ id: 1, email: "admin@example.com", name: "First", is_workspace_admin: true })
      .mockRejectedValueOnce(new Error("Second save failed"));

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("Your name"), { target: { value: "First" } });
    fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
    expect(await screen.findByRole("button", { name: /Saved/ })).toBeInTheDocument();

    // Save again inside the 2s "Saved" window and fail.
    fireEvent.change(screen.getByPlaceholderText("Your name"), { target: { value: "Second" } });
    fireEvent.click(screen.getByRole("button", { name: /Saved/ }));

    expect(await screen.findByText("Second save failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Saved/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save profile" })).toBeEnabled();
  });

  it("says the sessions were not revoked, and keeps the user signed in, when sign-out-everywhere fails", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    revokeSessionsMock.mockRejectedValue(new Error("Server unavailable"));

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /sign out of all devices/i }));
    fireEvent.click(await screen.findByRole("button", { name: /click again to confirm/i }));

    const alert = await screen.findByText(/Your sessions were not revoked/);
    expect(alert).toHaveTextContent("Server unavailable");
    // Not logged out locally, and back to the first click so it can be retried.
    expect(localStorage.getItem(TOKEN_KEY)).not.toBeNull();
    expect(screen.getByRole("button", { name: /sign out of all devices/i })).toBeEnabled();
  });

  it("reports a failed saved-token delete on its own row without locking the other rows", async () => {
    orgsMineMock.mockResolvedValue([]);
    installationsListMock.mockResolvedValue([]);
    tokensListMock.mockResolvedValue([
      { org: "acme", label: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
      { org: "beta", label: null, created_at: "2026-01-02T00:00:00Z", updated_at: "2026-01-02T00:00:00Z" },
    ]);
    configGetAllMock.mockResolvedValue({ worker_poll_seconds: "5", registration_enabled: "true" });
    const acmeGate = deferred<void>();
    const betaGate = deferred<void>();
    tokensDeleteMock.mockImplementation((org: string) => (org === "acme" ? acmeGate.promise : betaGate.promise));

    renderPage();

    const deleteAcme = await screen.findByRole("button", { name: "Delete token for acme" });
    const deleteBeta = screen.getByRole("button", { name: "Delete token for beta" });
    fireEvent.click(deleteAcme);
    fireEvent.click(deleteBeta);
    await waitFor(() => expect(deleteAcme).toBeDisabled());
    expect(deleteBeta).toBeDisabled();

    // The first delete fails after the second has started: its error must land on its own row.
    await act(async () => {
      acmeGate.reject(new Error("Token is in use"));
      await acmeGate.promise.catch(() => {});
    });

    const acmeRow = deleteAcme.closest("tr")!;
    const betaRow = deleteBeta.closest("tr")!;
    await waitFor(() => expect(within(acmeRow).getByRole("alert")).toHaveTextContent("Token is in use"));
    expect(deleteAcme).toBeEnabled();
    expect(deleteBeta).toBeDisabled();
    expect(within(betaRow).queryByRole("alert")).not.toBeInTheDocument();

    // A successful delete refreshes the list.
    const listCalls = tokensListMock.mock.calls.length;
    await act(async () => {
      betaGate.resolve();
      await betaGate.promise;
    });
    await waitFor(() => expect(tokensListMock.mock.calls.length).toBeGreaterThan(listCalls));
    await waitFor(() => expect(deleteBeta).toBeEnabled());
  });

});
