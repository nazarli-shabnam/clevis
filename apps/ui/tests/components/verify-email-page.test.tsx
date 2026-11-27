import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
}));

const verifyEmailMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: { auth: { verifyEmail: (...args: unknown[]) => verifyEmailMock(...args) } },
}));

import VerifyEmailPage from "@/app/verify-email/page";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <VerifyEmailPage />
    </QueryClientProvider>,
  );
}

function fill(password: string, confirm = password) {
  fireEvent.change(screen.getByPlaceholderText("At least 12 characters"), { target: { value: password } });
  fireEvent.change(screen.getByPlaceholderText("Repeat password"), { target: { value: confirm } });
  fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
}

describe("VerifyEmailPage", () => {
  beforeEach(() => {
    verifyEmailMock.mockReset();
    mockSearchParams = new URLSearchParams({ token: "good-token" });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("does not verify on load: the emailed link only opens the password form", () => {
    renderPage();

    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(verifyEmailMock).not.toHaveBeenCalled();
  });

  it("submits the token with the chosen password and then leads to sign in", async () => {
    verifyEmailMock.mockResolvedValue({ ok: true });
    renderPage();

    fill("a-long-enough-password");

    await screen.findByText(/your email is verified/i);
    expect(verifyEmailMock).toHaveBeenCalledWith("good-token", "a-long-enough-password");
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("shows Verifying… and disables the button while the request is in flight", async () => {
    verifyEmailMock.mockReturnValue(new Promise(() => {}));
    renderPage();

    fill("a-long-enough-password");

    expect(await screen.findByRole("button", { name: /Verifying…/ })).toBeDisabled();
  });

  it("rejects a short or mismatched password without calling the API", async () => {
    renderPage();

    fill("short");
    expect(await screen.findByText(/at least 12 characters/i, { selector: "p[role=alert]" })).toBeInTheDocument();

    fill("a-long-enough-password", "a-different-password");
    expect(await screen.findByText(/do not match/i)).toBeInTheDocument();
    expect(verifyEmailMock).not.toHaveBeenCalled();
  });

  it("shows the server's error message when verification fails", async () => {
    verifyEmailMock.mockRejectedValue(new Error("Invalid or expired verification link"));
    renderPage();

    fill("a-long-enough-password");

    await screen.findByText(/invalid or expired verification link/i);
  });

  it("shows an error and no form when the URL has no token", () => {
    mockSearchParams = new URLSearchParams();
    renderPage();

    expect(screen.getByText(/missing its token/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Clevis" })).toHaveAttribute("href", "/");
    expect(screen.queryByPlaceholderText("Repeat password")).not.toBeInTheDocument();
  });
});
