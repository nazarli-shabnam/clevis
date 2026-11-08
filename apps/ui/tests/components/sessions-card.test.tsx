import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const sessionsMock = vi.fn();
const revokeMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    auth: {
      sessions: (...a: unknown[]) => sessionsMock(...a),
      revokeSession: (...a: unknown[]) => revokeMock(...a),
    },
  },
}));

import { SessionsCard } from "@/components/sessions-card";

const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36";
const SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1";
const session = (id: string, over: Record<string, unknown> = {}) => ({
  id, created_at: "2026-01-01T00:00:00Z", last_seen_at: "2026-01-02T00:00:00Z",
  user_agent: CHROME, ip: "203.0.113.7", current: false, ...over,
});

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SessionsCard />
    </QueryClientProvider>,
  );
}

describe("SessionsCard", () => {
  beforeEach(() => {
    sessionsMock.mockReset();
    revokeMock.mockReset();
    revokeMock.mockResolvedValue(undefined);
  });
  afterEach(cleanup);

  it("lists each session with its device, IP and marks this device without a Revoke button", async () => {
    sessionsMock.mockResolvedValue([session("a", { current: true }), session("b", { user_agent: SAFARI, ip: "198.51.100.2" })]);
    renderCard();

    expect(await screen.findByText("Safari on iOS")).toBeInTheDocument();
    expect(screen.getByText("Chrome on Windows")).toBeInTheDocument();
    expect(screen.getByText("This device")).toBeInTheDocument();
    expect(screen.getByText(/198\.51\.100\.2/)).toBeInTheDocument();
    // Only the other device can be revoked from here.
    expect(screen.getAllByRole("button", { name: /^Revoke/ })).toHaveLength(1);
  });

  it("revokes just the chosen session and then reloads the list", async () => {
    sessionsMock.mockResolvedValueOnce([session("a", { current: true }), session("b", { user_agent: SAFARI })]);
    sessionsMock.mockResolvedValue([session("a", { current: true })]);
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: /Revoke the Safari on iOS session/ }));

    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith("b"));
    await waitFor(() => expect(screen.queryByText("Safari on iOS")).not.toBeInTheDocument());
    expect(screen.getByText("This device")).toBeInTheDocument();
  });

  it("shows an error and keeps the session when the revoke fails", async () => {
    sessionsMock.mockResolvedValue([session("b", { user_agent: SAFARI })]);
    revokeMock.mockRejectedValue(new Error("boom"));
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: /Revoke/ }));

    expect(await screen.findByText(/That session was not revoked. boom/)).toBeInTheDocument();
    expect(screen.getByText("Safari on iOS")).toBeInTheDocument();
  });

  it("shows a retryable error rather than an empty list when loading fails", async () => {
    sessionsMock.mockRejectedValueOnce(new Error("down"));
    sessionsMock.mockResolvedValue([session("a", { current: true })]);
    renderCard();

    expect(await screen.findByText("down")).toBeInTheDocument();
    expect(screen.queryByText(/No other sessions/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("This device")).toBeInTheDocument();
  });

  it("falls back for a missing user agent or IP", async () => {
    sessionsMock.mockResolvedValue([session("b", { user_agent: null, ip: null })]);
    renderCard();

    expect(await screen.findByText("Unknown device")).toBeInTheDocument();
    expect(screen.getByText(/Unknown IP/)).toBeInTheDocument();
  });
});
