import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const feedMock = vi.fn();
const markReadMock = vi.fn();
let scope: { kind: "org" | "personal"; login: string } | null = { kind: "org", login: "acme" };

vi.mock("@/lib/active-scope", () => ({ useActiveScope: () => ({ scope }) }));
vi.mock("@/lib/api/client", () => ({
  api: {
    notifications: {
      feed: (...a: unknown[]) => feedMock(...a),
      markRead: (...a: unknown[]) => markReadMock(...a),
    },
  },
}));

import { NotificationBell } from "@/components/notification-bell";
import { SinceLastVisitCard } from "@/components/since-last-visit-card";

const item = (id: string, kind: string, read = false, over: Record<string, unknown> = {}) => ({
  id, kind, at: "2026-01-01T00:00:00Z", title: `title ${id}`, detail: "", href: "/security", read, ...over,
});
const feed = (items: ReturnType<typeof item>[]) => ({
  org: "acme", items, unread_count: items.filter((i) => !i.read).length, last_read_at: null,
});

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("NotificationBell", () => {
  beforeEach(() => {
    feedMock.mockReset();
    markReadMock.mockReset();
    markReadMock.mockResolvedValue(null);
    scope = { kind: "org", login: "acme" };
  });
  afterEach(cleanup);

  it("shows the unread count on the bell and lists items when opened", async () => {
    feedMock.mockResolvedValue(feed([item("a", "critical_alert"), item("b", "score_drop", true)]));
    wrap(<NotificationBell />);
    const bell = await screen.findByRole("button", { name: "Notifications, 1 unread" });
    fireEvent.click(bell);
    await waitFor(() => expect(screen.getByText("title a")).toBeInTheDocument());
    expect(screen.getByText("title b")).toBeInTheDocument();
    expect(screen.getByText(/Critical alert ·/)).toBeInTheDocument();
  });

  it("marks everything read and refetches", async () => {
    feedMock.mockResolvedValueOnce(feed([item("a", "job_failed")])).mockResolvedValue(feed([item("a", "job_failed", true)]));
    wrap(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /Notifications, 1 unread/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(markReadMock).toHaveBeenCalledWith("acme"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Notifications" })).toBeInTheDocument());
  });

  it("says so when there is nothing new, and disables mark-all-read", async () => {
    feedMock.mockResolvedValue(feed([]));
    wrap(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "Notifications" }));
    await waitFor(() => expect(screen.getByText(/Nothing new in the last 14 days/)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
  });

  it("shows an error instead of an empty list when the feed fails", async () => {
    feedMock.mockRejectedValue(new Error("boom"));
    wrap(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: "Notifications" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("boom"));
    expect(screen.queryByText(/Nothing new/)).not.toBeInTheDocument();
  });

  it("renders nothing and fetches nothing for a personal scope", () => {
    scope = { kind: "personal", login: "octocat" };
    const { container } = wrap(<NotificationBell />);
    expect(container).toBeEmptyDOMElement();
    expect(feedMock).not.toHaveBeenCalled();
  });
});

describe("SinceLastVisitCard", () => {
  beforeEach(() => {
    feedMock.mockReset();
    scope = { kind: "org", login: "acme" };
  });
  afterEach(cleanup);

  it("summarises unread items by kind and ignores read ones", async () => {
    feedMock.mockResolvedValue(
      feed([item("a", "critical_alert"), item("b", "critical_alert"), item("c", "score_drop"), item("d", "job_failed", true)]),
    );
    wrap(<SinceLastVisitCard />);
    await waitFor(() => expect(screen.getByText("2 new critical alerts, 1 score drop")).toBeInTheDocument());
  });

  it("renders nothing when everything is read", async () => {
    feedMock.mockResolvedValue(feed([item("a", "score_drop", true)]));
    const { container } = wrap(<SinceLastVisitCard />);
    await waitFor(() => expect(feedMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
