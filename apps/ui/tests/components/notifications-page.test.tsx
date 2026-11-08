import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const feedMock = vi.fn();
const markReadMock = vi.fn();
const dismissMock = vi.fn();
let scope: { kind: "org" | "personal"; login: string } | null = { kind: "org", login: "acme" };

vi.mock("@/lib/active-scope", () => ({ useActiveScope: () => ({ scope }) }));
vi.mock("@/lib/api/client", () => ({
  api: {
    notifications: {
      feed: (...a: unknown[]) => feedMock(...a),
      markRead: (...a: unknown[]) => markReadMock(...a),
      dismiss: (...a: unknown[]) => dismissMock(...a),
    },
  },
}));

import NotificationsPage from "@/app/notifications/page";

const item = (id: string, kind: string, read = false, over: Record<string, unknown> = {}) => ({
  id, kind, at: "2026-01-01T00:00:00Z", title: `title ${id}`, detail: "", href: "/security", read, ...over,
});
const feed = (items: ReturnType<typeof item>[]) => ({
  org: "acme", items, unread_count: items.filter((i) => !i.read).length, last_read_at: null,
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NotificationsPage />
    </QueryClientProvider>,
  );
}

describe("NotificationsPage", () => {
  beforeEach(() => {
    [feedMock, markReadMock, dismissMock].forEach((m) => m.mockReset());
    markReadMock.mockResolvedValue(null);
    dismissMock.mockResolvedValue(null);
    scope = { kind: "org", login: "acme" };
  });
  afterEach(cleanup);

  it("asks the API for the long window, not the bell's default one", async () => {
    feedMock.mockResolvedValue(feed([item("a", "score_drop")]));
    renderPage();

    await screen.findByText("title a");
    expect(feedMock).toHaveBeenCalledWith("acme", { days: 90, limit: 200 });
  });

  it("filters by kind and by unread, and says so when nothing matches", async () => {
    feedMock.mockResolvedValue(
      feed([item("a", "critical_alert"), item("b", "score_drop", true), item("c", "job_failed")]),
    );
    renderPage();
    await screen.findByText("title a");

    fireEvent.click(screen.getByRole("button", { name: "Score drop" }));
    expect(screen.queryByText("title b")).not.toBeInTheDocument();
    expect(screen.getByText("title a")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Score drop" }));
    fireEvent.click(screen.getByLabelText("Unread only"));
    expect(screen.queryByText("title b")).not.toBeInTheDocument(); // read
    expect(screen.getByText("title c")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Critical alert" }));
    fireEvent.click(screen.getByRole("button", { name: "Job failed" }));
    expect(screen.getByText(/No notifications match these filters/)).toBeInTheDocument();
  });

  it("marks one item read without marking the others, and refetches", async () => {
    feedMock.mockResolvedValueOnce(feed([item("a", "critical_alert"), item("b", "score_drop")]));
    feedMock.mockResolvedValue(feed([item("a", "critical_alert", true), item("b", "score_drop")]));
    renderPage();
    await screen.findByText("title a");

    fireEvent.click(screen.getByRole("button", { name: 'Mark "title a" as read' }));

    await waitFor(() => expect(dismissMock).toHaveBeenCalledWith("acme", "a"));
    expect(markReadMock).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("button", { name: 'Mark "title a" as read' })).not.toBeInTheDocument());
    // The other item still offers its own button.
    expect(screen.getByRole("button", { name: 'Mark "title b" as read' })).toBeInTheDocument();
  });

  it("marks everything read up to the newest item shown", async () => {
    feedMock.mockResolvedValue(feed([item("a", "critical_alert", false, { at: "2026-02-02T00:00:00Z" })]));
    renderPage();
    await screen.findByText("title a");

    fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));

    await waitFor(() => expect(markReadMock).toHaveBeenCalledWith("acme", "2026-02-02T00:00:00Z"));
  });

  it("shows an error when a single mark-read fails, and keeps the item unread", async () => {
    feedMock.mockResolvedValue(feed([item("a", "critical_alert")]));
    dismissMock.mockRejectedValue(new Error("boom"));
    renderPage();
    await screen.findByText("title a");

    fireEvent.click(screen.getByRole("button", { name: 'Mark "title a" as read' }));

    expect(await screen.findByText(/Couldn't mark that notification as read: boom/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: 'Mark "title a" as read' })).toBeInTheDocument();
  });

  it("shows a retryable error when the feed fails, not an empty state", async () => {
    feedMock.mockRejectedValueOnce(new Error("feed down"));
    feedMock.mockResolvedValue(feed([item("a", "score_drop")]));
    renderPage();

    expect(await screen.findByText("feed down")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing in the last/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("title a")).toBeInTheDocument();
  });

  it("says when the feed is at the cap and older items are not listed", async () => {
    feedMock.mockResolvedValue(feed(Array.from({ length: 200 }, (_, i) => item(`n${i}`, "score_drop", true))));
    renderPage();

    expect(await screen.findByText(/Showing the newest 200 notifications/)).toBeInTheDocument();
  });

  it("makes no request for a personal scope and explains why", async () => {
    scope = { kind: "personal", login: "octocat" };
    renderPage();

    expect(await screen.findByText(/belong to an organization/)).toBeInTheDocument();
    expect(feedMock).not.toHaveBeenCalled();
  });
});
