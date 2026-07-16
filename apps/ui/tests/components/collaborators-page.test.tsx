import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const orgsMineMock = vi.fn();
const replaceMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: (...args: unknown[]) => orgsMineMock(...args) },
  },
}));

import CollaboratorsPage from "@/app/collaborators/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CollaboratorsPage />
    </QueryClientProvider>,
  );
}

describe("CollaboratorsPage", () => {
  beforeEach(() => {
    orgsMineMock.mockReset();
    replaceMock.mockReset();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("redirects to the members page for an org where the user is admin", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);

    renderPage();

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/settings/org/acme/members"),
    );
  });

  it("prefers the saved default_org when it's an admin org", async () => {
    localStorage.setItem("default_org", "widgets");
    orgsMineMock.mockResolvedValue([
      { org_login: "acme", role: "admin" },
      { org_login: "widgets", role: "admin" },
    ]);

    renderPage();

    await waitFor(() =>
      expect(replaceMock).toHaveBeenCalledWith("/settings/org/widgets/members"),
    );
  });

  it("shows a message instead of redirecting when the user has no admin org", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/no organization to manage/i)).toBeInTheDocument(),
    );
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows an error message instead of a false no-admin-org state when the fetch fails", async () => {
    orgsMineMock.mockRejectedValue(new Error("network error"));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/couldn.t load your organizations/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/no organization to manage/i)).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
