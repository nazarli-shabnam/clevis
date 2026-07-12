import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const tokensUpsertMock = vi.fn();
const cacheListMock = vi.fn();
const cacheClearMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => ({ repo: "acme~demo" }),
}));

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: (...args: unknown[]) => tokensUpsertMock(...args),
    },
    cache: {
      list: (...args: unknown[]) => cacheListMock(...args),
      clear: (...args: unknown[]) => cacheClearMock(...args),
    },
  },
}));

import CachePage from "@/app/repos/[repo]/cache/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <CachePage />
    </QueryClientProvider>,
  );
}

describe("CachePage", () => {
  beforeEach(() => {
    tokensResolveMock.mockReset();
    tokensUpsertMock.mockReset();
    cacheListMock.mockReset();
    cacheClearMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("allows a dry-run clear with an actor but no token entered", async () => {
    cacheClearMock.mockResolvedValue({ queued: false, dry_run: true });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("actor"), { target: { value: "me@example.com" } });

    const dryRunButton = screen.getByRole("button", { name: /dry run/i });
    await waitFor(() => expect(dryRunButton).not.toBeDisabled());

    fireEvent.click(dryRunButton);

    await waitFor(() =>
      expect(cacheClearMock).toHaveBeenCalledWith("acme", "demo", {
        token: "",
        actor: "me@example.com",
        dry_run: true,
      }),
    );
  });

  it("keeps the Clear button disabled until an actor is entered", async () => {
    renderPage();
    expect(screen.getByRole("button", { name: /^clear$/i })).toBeDisabled();
  });
});
