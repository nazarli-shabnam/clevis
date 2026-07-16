import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("requires a second click on Clear before actually clearing caches", async () => {
    cacheClearMock.mockResolvedValue({ queued: true, dry_run: false, job_id: 7 });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("actor"), { target: { value: "me@example.com" } });
    const clearButton = screen.getByRole("button", { name: /^clear$/i });
    await waitFor(() => expect(clearButton).not.toBeDisabled());

    fireEvent.click(clearButton);

    // First click only arms the button — no request fired yet.
    expect(cacheClearMock).not.toHaveBeenCalled();
    const confirmButton = await screen.findByRole("button", { name: /confirm clear/i });
    expect(screen.getByText(/click again to permanently delete/i)).toBeInTheDocument();

    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(cacheClearMock).toHaveBeenCalledWith("acme", "demo", {
        token: "",
        actor: "me@example.com",
        dry_run: false,
      }),
    );
  });

  it("disarms the confirm state if the actor is edited before confirming", async () => {
    renderPage();

    fireEvent.change(screen.getByPlaceholderText("actor"), { target: { value: "me@example.com" } });
    const clearButton = screen.getByRole("button", { name: /^clear$/i });
    await waitFor(() => expect(clearButton).not.toBeDisabled());

    fireEvent.click(clearButton);
    await screen.findByRole("button", { name: /confirm clear/i });

    fireEvent.change(screen.getByPlaceholderText("actor"), { target: { value: "someone-else@example.com" } });

    expect(screen.queryByRole("button", { name: /confirm clear/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^clear$/i })).toBeInTheDocument();
    expect(cacheClearMock).not.toHaveBeenCalled();
  });

  it("disarms the confirm state if Load caches is clicked before confirming", async () => {
    cacheListMock.mockResolvedValue({ actions_caches: [] });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("actor"), { target: { value: "me@example.com" } });
    const clearButton = screen.getByRole("button", { name: /^clear$/i });
    await waitFor(() => expect(clearButton).not.toBeDisabled());

    fireEvent.click(clearButton);
    await screen.findByRole("button", { name: /confirm clear/i });

    fireEvent.click(screen.getByRole("button", { name: /load caches/i }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /confirm clear/i })).not.toBeInTheDocument(),
    );
    expect(cacheClearMock).not.toHaveBeenCalled();
  });

  it("auto-disarms the confirm state after a few seconds of inactivity", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      renderPage();

      fireEvent.change(screen.getByPlaceholderText("actor"), { target: { value: "me@example.com" } });
      const clearButton = screen.getByRole("button", { name: /^clear$/i });
      await waitFor(() => expect(clearButton).not.toBeDisabled());

      fireEvent.click(clearButton);
      await screen.findByRole("button", { name: /confirm clear/i });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });

      expect(screen.queryByRole("button", { name: /confirm clear/i })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: /^clear$/i })).toBeInTheDocument();
      expect(cacheClearMock).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
