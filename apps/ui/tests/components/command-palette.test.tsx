import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const setScope = vi.fn();
const listMock = vi.fn();
const overviewMock = vi.fn();
const markReadMock = vi.fn();
const toastSuccess = vi.fn();
const toastInfo = vi.fn();
const toastError = vi.fn();
let scope: { kind: "org" | "personal"; login: string } | null = { kind: "org", login: "acme" };
let isAdmin = false;

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ user: { email: "me@acme.test", is_workspace_admin: isAdmin } }) }));
vi.mock("@/components/ui/toast", () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    info: (...a: unknown[]) => toastInfo(...a),
    error: (...a: unknown[]) => toastError(...a),
  },
}));
vi.mock("@/lib/active-scope", () => ({ useActiveScope: () => ({ scope, setScope }) }));
vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: () => Promise.resolve([{ org_login: "acme", role: "admin" }, { org_login: "globex", role: "member" }]) },
    installations: { list: () => Promise.resolve([{ account_type: "User", account_login: "octocat" }]) },
    tokens: { resolve: () => Promise.reject(new Error("none")) },
    repos: { list: (...a: unknown[]) => listMock(...a) },
    analytics: { overview: (...a: unknown[]) => overviewMock(...a) },
    notifications: { markRead: (...a: unknown[]) => markReadMock(...a) },
  },
}));

import { CommandPalette } from "@/components/command-palette";

function renderPalette() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CommandPalette />
    </QueryClientProvider>,
  );
}

const openWithKey = () => fireEvent.keyDown(window, { key: "k", ctrlKey: true });

describe("CommandPalette", () => {
  beforeEach(() => {
    push.mockReset();
    setScope.mockReset();
    listMock.mockReset();
    overviewMock.mockReset();
    markReadMock.mockReset();
    toastSuccess.mockReset();
    toastInfo.mockReset();
    toastError.mockReset();
    scope = { kind: "org", login: "acme" };
    isAdmin = false;
    listMock.mockResolvedValue({ org: "acme", repos: [{ name: "api", full_name: "acme/api", description: "the api" }] });
  });
  afterEach(cleanup);

  it("opens with Ctrl+K, fetches repos only then, and navigates to a repo on Enter", async () => {
    renderPalette();
    expect(listMock).not.toHaveBeenCalled();
    openWithKey();
    const input = await screen.findByRole("combobox");
    await waitFor(() => expect(listMock).toHaveBeenCalledWith("acme", ""));
    fireEvent.change(input, { target: { value: "acme/api" } });
    await waitFor(() => expect(screen.getByRole("option", { name: /acme\/api/ })).toBeInTheDocument());
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/repos/acme~api");
  });

  it("also opens with Cmd+K and toggles closed again", async () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "K", metaKey: true });
    await screen.findByRole("combobox");
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
  });

  it("ignores Shift/Alt variants and key repeat", async () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true, shiftKey: true });
    fireEvent.keyDown(window, { key: "k", ctrlKey: true, altKey: true });
    fireEvent.keyDown(window, { key: "k", ctrlKey: true, repeat: true });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("does not act on Enter while an IME composition is in progress", async () => {
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(push).not.toHaveBeenCalled();
  });

  it("moves the selection with the arrow keys", async () => {
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    const options = () => screen.getAllByRole("option");
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(options()[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
  });

  it("switches account without navigating, offering only the accounts that are not active", async () => {
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "switch" } });
    await waitFor(() => expect(screen.getByRole("option", { name: /Switch to globex/ })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: /Switch to acme/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: /Switch to octocat/ }));
    expect(setScope).toHaveBeenCalledWith({ kind: "personal", login: "octocat" });
    expect(push).not.toHaveBeenCalled();
  });

  it("hides Audit Log from non-admins and resolves Collaborators to the org members page", async () => {
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "audit" } });
    await waitFor(() => expect(screen.getByText("No matches")).toBeInTheDocument());
    fireEvent.change(input, { target: { value: "collab" } });
    await waitFor(() => expect(screen.getByRole("option", { name: /Collaborators/ })).toBeInTheDocument());
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/settings/org/acme/members");
  });

  it("does not fetch repositories for a personal scope", async () => {
    scope = { kind: "personal", login: "octocat" };
    renderPalette();
    openWithKey();
    await screen.findByRole("combobox");
    expect(listMock).not.toHaveBeenCalled();
  });

  it("says so when the repositories could not be loaded, and still searches pages", async () => {
    listMock.mockRejectedValue(new Error("boom"));
    renderPalette();
    openWithKey();
    await waitFor(() => expect(screen.getByText(/Couldn't load acme's repositories/)).toBeInTheDocument());
    expect(screen.getByRole("option", { name: /Overview/ })).toBeInTheDocument();
  });

  it("offers quick actions to members of the active org and navigates for 'failing CI'", async () => {
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "failing" } });
    await waitFor(() => expect(screen.getByRole("option", { name: /Open failing CI/ })).toBeInTheDocument());
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/activity#ci-failures");
  });

  it("runs a scan from the palette and reports the result", async () => {
    overviewMock.mockResolvedValue({ owner: "acme", score: 83 });
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "run security scan" } });
    fireEvent.click(await screen.findByRole("option", { name: /Run security scan/ }));

    await waitFor(() => expect(overviewMock).toHaveBeenCalledWith("acme", ""));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith("Scan of acme finished: score 83."));
    expect(toastInfo).toHaveBeenCalledWith("Scanning acme…");
    expect(push).not.toHaveBeenCalled();
  });

  it("marks all notifications read and surfaces a failure", async () => {
    markReadMock.mockRejectedValue(new Error("nope"));
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "mark all" } });
    fireEvent.click(await screen.findByRole("option", { name: /Mark all notifications read/ }));

    await waitFor(() => expect(markReadMock).toHaveBeenCalledWith("acme"));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("nope"));
  });

  it("hides org actions when the caller is not a member of the active org", async () => {
    scope = { kind: "org", login: "not-mine" };
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "scan" } });
    await waitFor(() => expect(screen.getByText("No matches")).toBeInTheDocument());
  });

  it("offers the audit log filtered to me to workspace admins only", async () => {
    isAdmin = true;
    renderPalette();
    openWithKey();
    const input = await screen.findByRole("combobox");
    fireEvent.change(input, { target: { value: "filtered to me" } });
    await waitFor(() => expect(screen.getByRole("option", { name: /audit log filtered to me/i })).toBeInTheDocument());
    fireEvent.keyDown(input, { key: "Enter" });
    expect(push).toHaveBeenCalledWith("/audit?actor=me%40acme.test");
  });
});
