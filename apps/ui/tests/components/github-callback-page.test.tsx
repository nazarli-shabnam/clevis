import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
let searchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => searchParams,
}));

const lookupMock = vi.fn();
const syncMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    installations: {
      lookup: (...args: unknown[]) => lookupMock(...args),
      sync: (...args: unknown[]) => syncMock(...args),
    },
  },
}));

import GithubInstallCallbackPage from "@/app/settings/github-callback/page";

describe("GithubInstallCallbackPage", () => {
  beforeEach(() => {
    replace.mockClear();
    lookupMock.mockReset();
    syncMock.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("connects a personal (User) installation and redirects to settings", async () => {
    searchParams = new URLSearchParams({ installation_id: "42", setup_action: "install" });
    lookupMock.mockResolvedValue({ account_login: "shabnam", account_type: "User" });
    syncMock.mockResolvedValue({ synced: true, token_ref: "tok_x" });

    render(<GithubInstallCallbackPage />);

    await waitFor(() => expect(screen.getByText(/Connected/)).toBeInTheDocument());

    expect(lookupMock).toHaveBeenCalledWith(42);
    expect(syncMock).toHaveBeenCalledWith(
      { scope: "me" },
      { account_login: "shabnam", account_type: "User", installation_id: 42 },
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/settings?installed=1"), { timeout: 2000 });
  });

  it("connects an organization installation via the org sync endpoint", async () => {
    searchParams = new URLSearchParams({ installation_id: "7", setup_action: "install" });
    lookupMock.mockResolvedValue({ account_login: "acme", account_type: "Organization" });
    syncMock.mockResolvedValue({ synced: true, token_ref: "tok_y" });

    render(<GithubInstallCallbackPage />);

    await waitFor(() => expect(screen.getByText(/Connected/)).toBeInTheDocument());

    expect(syncMock).toHaveBeenCalledWith(
      { scope: "org", orgLogin: "acme" },
      { account_login: "acme", account_type: "Organization", installation_id: 7 },
    );
  });

  it("shows a pending-approval message and skips the API calls when setup_action is request", async () => {
    searchParams = new URLSearchParams({ installation_id: "9", setup_action: "request" });

    render(<GithubInstallCallbackPage />);

    await waitFor(() => expect(screen.getByText(/needs approval/i)).toBeInTheDocument());
    expect(lookupMock).not.toHaveBeenCalled();
    expect(syncMock).not.toHaveBeenCalled();
  });

  it("shows an error when GitHub didn't send an installation_id", async () => {
    searchParams = new URLSearchParams({ setup_action: "install" });

    render(<GithubInstallCallbackPage />);

    await waitFor(() => expect(screen.getByText(/didn't send a valid installation_id/)).toBeInTheDocument());
    expect(lookupMock).not.toHaveBeenCalled();
  });

  it("surfaces the API error message when sync fails (e.g. a brand-new org not yet recognized)", async () => {
    searchParams = new URLSearchParams({ installation_id: "7", setup_action: "install" });
    lookupMock.mockResolvedValue({ account_login: "acme", account_type: "Organization" });
    syncMock.mockRejectedValue(new Error("Org not found"));

    render(<GithubInstallCallbackPage />);

    await waitFor(() => expect(screen.getByText("Org not found")).toBeInTheDocument());
    const backButton = screen.getByRole("button", { name: /back to settings/i });
    expect(backButton).toBeInTheDocument();

    fireEvent.click(backButton);
    expect(replace).toHaveBeenCalledWith("/settings");
  });
});
