import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
}));

const verifyEmailMock = vi.fn();
const resendMock = vi.fn();
let signedIn = false;
let authLoading = false;

vi.mock("@/lib/api/client", () => ({
  api: {
    auth: {
      verifyEmail: (...args: unknown[]) => verifyEmailMock(...args),
      resendVerification: (...args: unknown[]) => resendMock(...args),
    },
  },
}));
vi.mock("@/lib/auth-context", () => ({
  useOptionalAuth: () => (signedIn ? { user: { id: 1, email: "a@b.c" }, isLoading: authLoading } : null),
}));

// What the API client throws for a rejected request: an Error that carries the HTTP status.
const rejected = (message: string, status: number) => Object.assign(new Error(message), { status });

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
    resendMock.mockReset();
    signedIn = false;
    authLoading = false;
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
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByPlaceholderText("Repeat password")).not.toBeInTheDocument();
  });

  describe("when the link cannot be used", () => {
    it("offers sign in, not a form, to someone who is signed out", async () => {
      verifyEmailMock.mockRejectedValue(rejected("Invalid or expired verification link", 400));
      renderPage();

      fill("a-long-enough-password");

      expect(await screen.findByText("Invalid or expired verification link")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
      expect(screen.queryByRole("button", { name: "Resend verification email" })).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText("Repeat password")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Back to Clevis" })).toHaveAttribute("href", "/");
    });

    it("lets a signed-in user ask for a new email right there", async () => {
      signedIn = true;
      verifyEmailMock.mockRejectedValue(rejected("Invalid or expired verification link", 400));
      resendMock.mockResolvedValue({ ok: true, already_verified: false });
      renderPage();

      fill("a-long-enough-password");
      fireEvent.click(await screen.findByRole("button", { name: "Resend verification email" }));

      expect(await screen.findByText(/new verification link is on its way/i)).toBeInTheDocument();
      expect(resendMock).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
    });

    it("says so when the email turns out to be verified already, and shows a failed resend", async () => {
      signedIn = true;
      mockSearchParams = new URLSearchParams();
      resendMock.mockResolvedValueOnce({ ok: true, already_verified: true });
      renderPage();

      fireEvent.click(screen.getByRole("button", { name: "Resend verification email" }));
      expect(await screen.findByText("Your email is already verified.")).toBeInTheDocument();
      cleanup();

      resendMock.mockRejectedValue(new Error("Email is not configured on this server"));
      renderPage();
      fireEvent.click(screen.getByRole("button", { name: "Resend verification email" }));
      expect(await screen.findByText("Email is not configured on this server")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Resend verification email" })).toBeInTheDocument();
    });

    it("keeps the form for a failure the user can retry with the same link", async () => {
      verifyEmailMock
        .mockRejectedValueOnce(new Error("Failed to fetch"))
        .mockRejectedValueOnce(rejected("Internal error", 500))
        .mockRejectedValueOnce(rejected("Password must be at most 72 bytes", 422))
        .mockRejectedValueOnce(rejected("Too many requests", 429));
      renderPage();

      for (const message of ["Failed to fetch", "Internal error", "Password must be at most 72 bytes", "Too many requests"]) {
        fill("a-long-enough-password");
        await screen.findByText(message);
        expect(screen.getByPlaceholderText("Repeat password")).toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Resend verification email" })).not.toBeInTheDocument();
      }
    });

    it("offers neither next step while the session is still being restored", () => {
      signedIn = true;
      authLoading = true;
      mockSearchParams = new URLSearchParams();
      renderPage();

      expect(screen.getByText(/missing its token/i)).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Sign in" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Resend verification email" })).not.toBeInTheDocument();
    });

    it("names the account the new link goes to", async () => {
      signedIn = true;
      mockSearchParams = new URLSearchParams();
      renderPage();

      expect(screen.getByText("a@b.c")).toBeInTheDocument();
    });
  });
});
