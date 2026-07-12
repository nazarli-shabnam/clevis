import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const tokensUpsertMock = vi.fn();
const analyticsOverviewMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: (...args: unknown[]) => tokensUpsertMock(...args),
    },
    analytics: {
      overview: (...args: unknown[]) => analyticsOverviewMock(...args),
    },
  },
}));

import SecurityPage from "@/app/security/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SecurityPage />
    </QueryClientProvider>,
  );
}

describe("SecurityPage", () => {
  beforeEach(() => {
    tokensResolveMock.mockReset();
    tokensUpsertMock.mockReset();
    analyticsOverviewMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("allows running a scan with no token entered (GitHub App fallback)", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 100,
      total_checks: 0,
      failed_checks: 0,
      repo_count: 0,
      checks: [],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());

    fireEvent.click(scanButton);

    await waitFor(() => expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", ""));
  });
});
