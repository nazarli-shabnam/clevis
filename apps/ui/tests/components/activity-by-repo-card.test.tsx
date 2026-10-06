import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const summaryMock = vi.fn();
const openStreamMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: { github: { activitySummary: (...a: unknown[]) => summaryMock(...a) } },
  openActivityStream: (...a: unknown[]) => openStreamMock(...a),
}));

import { ActivityByRepoCard, topRepos } from "@/components/activity-by-repo-card";

const summary = (over: Record<string, unknown> = {}) => ({
  org: "acme",
  days: 7,
  connected: true,
  generated_at: "2026-01-01T00:00:00Z",
  totals: [
    { repo: "acme/api", event_type: "push", count: 5 },
    { repo: "acme/api", event_type: "issues", count: 2 },
    { repo: "acme/web", event_type: "push", count: 9 },
  ],
  ...over,
});

function sse(...chunks: string[]) {
  const enc = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(c) {
        chunks.forEach((x) => c.enqueue(enc.encode(x)));
        // left open: a live stream
      },
    }),
    { status: 200 },
  );
}

function renderCard(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}>
      <ActivityByRepoCard org="acme" />
    </QueryClientProvider>,
  );
}

describe("topRepos", () => {
  it("totals per repo, busiest first, with event types busiest first", () => {
    const rows = topRepos(summary());
    expect(rows.map((r) => [r.repo, r.total])).toEqual([
      ["acme/web", 9],
      ["acme/api", 7],
    ]);
    expect(rows[1].byType).toEqual([
      ["push", 5],
      ["issues", 2],
    ]);
  });

  it("caps the list", () => {
    const totals = Array.from({ length: 12 }, (_, i) => ({ repo: `acme/r${i}`, event_type: "push", count: i + 1 }));
    expect(topRepos(summary({ totals }), 8)).toHaveLength(8);
  });
});

describe("ActivityByRepoCard", () => {
  beforeEach(() => {
    summaryMock.mockReset();
    openStreamMock.mockReset();
  });
  afterEach(cleanup);

  it("shows repo totals and goes live, applying a pushed update", async () => {
    summaryMock.mockResolvedValue(summary());
    const pushed = summary({ totals: [{ repo: "acme/cli", event_type: "push", count: 42 }] });
    openStreamMock.mockResolvedValue(sse(`event: activity_summary\ndata: ${JSON.stringify(pushed)}\n\n`));
    renderCard();

    await waitFor(() => expect(screen.getByText("Live")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("acme/cli")).toBeInTheDocument());
    expect(screen.queryByText("acme/web")).not.toBeInTheDocument();
    expect(openStreamMock).toHaveBeenCalledWith("acme", 7, expect.any(AbortSignal));
  });

  it("does not open a stream for an org with no webhook data and explains why", async () => {
    summaryMock.mockResolvedValue(summary({ connected: false, totals: [] }));
    renderCard();
    await waitFor(() => expect(screen.getByText(/needs the GitHub App installed on acme/)).toBeInTheDocument());
    expect(openStreamMock).not.toHaveBeenCalled();
  });

  it("aborts the stream on unmount", async () => {
    summaryMock.mockResolvedValue(summary());
    let signal: AbortSignal | undefined;
    openStreamMock.mockImplementation((_o: string, _d: number, s: AbortSignal) => {
      signal = s;
      return Promise.resolve(sse());
    });
    const { unmount } = renderCard();
    await waitFor(() => expect(signal).toBeDefined());
    act(() => unmount());
    expect(signal!.aborted).toBe(true);
  });

  it("reconnects when the server ends the stream at its duration cap", async () => {
    summaryMock.mockResolvedValue(summary());
    openStreamMock.mockImplementation(() => Promise.resolve(new Response("", { status: 200 })));
    renderCard();
    await waitFor(() => expect(openStreamMock.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: 3000 });
  });

  it("shows reconnecting when the stream request fails, and keeps the snapshot", async () => {
    summaryMock.mockResolvedValue(summary());
    openStreamMock.mockResolvedValue(new Response("nope", { status: 503 }));
    renderCard();
    await waitFor(() => expect(screen.getByText("Reconnecting…")).toBeInTheDocument());
    expect(screen.getByText("acme/web")).toBeInTheDocument();
  });

  it("surfaces a snapshot error with retry", async () => {
    summaryMock.mockRejectedValue(new Error("boom"));
    renderCard();
    await waitFor(() => expect(screen.getByText("boom")).toBeInTheDocument());
  });
});
