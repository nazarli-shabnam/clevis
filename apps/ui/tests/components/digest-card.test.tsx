import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const digestMock = vi.fn();
const setDigestMock = vi.fn();
const previewMock = vi.fn();
const testMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: {
      digest: (...a: unknown[]) => digestMock(...a),
      setDigest: (...a: unknown[]) => setDigestMock(...a),
      digestPreview: (...a: unknown[]) => previewMock(...a),
      sendTestDigest: (...a: unknown[]) => testMock(...a),
    },
  },
}));

import { DigestCard } from "@/components/digest-card";
import type { DigestSettings } from "@/lib/api/types";

const settings = (over: Partial<DigestSettings> = {}): DigestSettings => ({
  enabled: null,
  effective: true,
  cadence: "weekly",
  instance_cadence: "weekly",
  recipients: null,
  resolved_recipients: ["admin@acme.com"],
  available_recipients: ["admin@acme.com", "dev@acme.com"],
  smtp_configured: true,
  ...over,
});

const testClient = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

function renderCard() {
  const qc = testClient();
  return render(
    <QueryClientProvider client={qc}>
      <DigestCard orgLogin="acme" />
    </QueryClientProvider>,
  );
}

describe("DigestCard", () => {
  beforeEach(() => {
    [digestMock, setDigestMock, previewMock, testMock].forEach((m) => m.mockReset());
    digestMock.mockResolvedValue(settings());
  });
  afterEach(cleanup);

  it("keeps a preview scoped to the org it was built for (#835)", async () => {
    previewMock.mockResolvedValue({ subject: "Acme digest", body: "acme body", empty: false });
    const qc = testClient();
    const { rerender } = render(
      <QueryClientProvider client={qc}>
        <DigestCard orgLogin="acme" />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByText("Acme digest")).toBeInTheDocument();

    rerender(
      <QueryClientProvider client={qc}>
        <DigestCard orgLogin="globex" />
      </QueryClientProvider>,
    );
    await screen.findByRole("button", { name: "Preview" });
    expect(screen.queryByText("Acme digest")).not.toBeInTheDocument();
  });

  it("lets an admin uncheck a stored recipient who is no longer a verified member (#835)", async () => {
    digestMock.mockResolvedValue(settings({ recipients: ["admin@acme.com", "gone@acme.com"] }));
    setDigestMock.mockResolvedValue(settings({ recipients: ["admin@acme.com"] }));
    renderCard();

    fireEvent.click(await screen.findByLabelText(/gone@acme.com/));

    await waitFor(() => expect(setDigestMock).toHaveBeenCalledWith("acme", { recipients: ["admin@acme.com"] }));
  });

  it("lets an admin opt the org out of the instance cadence (#835)", async () => {
    setDigestMock.mockResolvedValue(settings({ enabled: false, effective: false, cadence: null }));
    renderCard();

    fireEvent.change(await screen.findByLabelText(/Email this organization/), { target: { value: "off" } });

    await waitFor(() => expect(setDigestMock).toHaveBeenCalledWith("acme", { enabled: false }));
    expect(await screen.findByText(/Not sent/)).toBeInTheDocument();
  });

  it("chooses recipients from the verified members", async () => {
    setDigestMock.mockResolvedValue(settings({ recipients: ["admin@acme.com", "dev@acme.com"] }));
    renderCard();

    fireEvent.click(await screen.findByLabelText("dev@acme.com"));
    await waitFor(() => expect(setDigestMock).toHaveBeenCalledWith("acme", { recipients: ["admin@acme.com", "dev@acme.com"] }));
  });

  it("falls back to every admin when the last chosen recipient is unchecked", async () => {
    digestMock.mockResolvedValue(settings({ recipients: ["dev@acme.com"], resolved_recipients: ["dev@acme.com"] }));
    setDigestMock.mockResolvedValue(settings());
    renderCard();

    fireEvent.click(await screen.findByLabelText("dev@acme.com"));
    await waitFor(() => expect(setDigestMock).toHaveBeenCalledWith("acme", { recipients: null }));
  });

  it("previews the digest and sends a test to the caller", async () => {
    previewMock.mockResolvedValue({ subject: "[Clevis] acme weekly digest", body: "Security score: 80/100", empty: false });
    testMock.mockResolvedValue({ sent_to: "admin@acme.com" });
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByText("Security score: 80/100")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Send test to me" }));
    expect(await screen.findByText(/Sent to admin@acme.com/)).toBeInTheDocument();
  });

  it("disables the test send and says why when SMTP is not configured", async () => {
    digestMock.mockResolvedValue(settings({ smtp_configured: false }));
    renderCard();

    expect(await screen.findByRole("button", { name: "Send test to me" })).toBeDisabled();
    expect(screen.getByText(/SMTP\) is not configured/)).toBeInTheDocument();
  });

  it("shows an error with a retry when the settings cannot be loaded", async () => {
    digestMock.mockRejectedValueOnce(new Error("boom"));
    renderCard();

    expect(await screen.findByText(/Couldn't load the digest settings: boom/)).toBeInTheDocument();
    digestMock.mockResolvedValue(settings());
    fireEvent.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByLabelText(/Email this organization/)).toBeInTheDocument();
  });

  it("resets a chosen recipient list back to every verified admin", async () => {
    digestMock.mockResolvedValue(settings({ recipients: ["dev@acme.com"], resolved_recipients: ["dev@acme.com"] }));
    setDigestMock.mockResolvedValue(settings());
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Reset to every verified admin" }));
    await waitFor(() => expect(setDigestMock).toHaveBeenCalledWith("acme", { recipients: null }));
  });

  it("flags an empty preview and shows the busy labels while building and sending", async () => {
    let finishPreview: (v: unknown) => void = () => {};
    let finishTest: (v: unknown) => void = () => {};
    previewMock.mockReturnValue(new Promise((r) => (finishPreview = r)));
    testMock.mockReturnValue(new Promise((r) => (finishTest = r)));
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByRole("button", { name: "Building…" })).toBeDisabled();
    finishPreview({ subject: "subj", body: "body", empty: true });
    expect(await screen.findByText(/No scan has run yet/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Send test to me" }));
    expect(await screen.findByRole("button", { name: "Sending…" })).toBeDisabled();
    finishTest({ sent_to: "admin@acme.com" });
    expect(await screen.findByText(/Sent to admin@acme.com/)).toBeInTheDocument();
  });

  it("surfaces an error from saving, previewing or sending", async () => {
    setDigestMock.mockRejectedValue(new Error("save failed"));
    renderCard();

    fireEvent.change(await screen.findByLabelText(/Email this organization/), { target: { value: "on" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("save failed");
  });
});
