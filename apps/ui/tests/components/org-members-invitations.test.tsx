import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const listMock = vi.fn();
const createMock = vi.fn();
const createManyMock = vi.fn();
const regenerateMock = vi.fn();
const resendMock = vi.fn();
const revokeMock = vi.fn();
const digestMock = vi.fn();
const digest = (smtp_configured: boolean) => ({
  enabled: null,
  effective: false,
  cadence: null,
  instance_cadence: "off",
  recipients: null,
  resolved_recipients: [],
  available_recipients: [],
  smtp_configured,
});

vi.mock("@/lib/api/client", () => ({
  api: {
    invitations: {
      list: (...args: unknown[]) => listMock(...args),
      create: (...args: unknown[]) => createMock(...args),
      createMany: (...args: unknown[]) => createManyMock(...args),
      regenerate: (...args: unknown[]) => regenerateMock(...args),
      resend: (...args: unknown[]) => resendMock(...args),
      revoke: (...args: unknown[]) => revokeMock(...args),
    },
    collab: {
      members: vi.fn().mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: false }),
      outsideCollaborators: vi.fn(),
      invitations: vi.fn().mockResolvedValue({ org: "acme", invitations: [] }),
      permissionAudit: vi.fn(),
      inactiveMembers: vi.fn(),
      membership: vi.fn(),
    },
    tokens: { resolve: vi.fn().mockRejectedValue(new Error("no saved token")) },
    orgs: {
      mine: vi.fn().mockResolvedValue([{ org_login: "acme", role: "admin" }]),
      digest: (...args: unknown[]) => digestMock(...args),
    },
  },
}));

import OrgMembersPage from "@/app/settings/org/[login]/members/page";

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const inFuture = (ms: number) => new Date(Date.now() + ms).toISOString();

function invitation(id: number, email: string, status: string, expires_at: string) {
  return { id, org_id: 1, email, status, created_at: "2026-09-01T00:00:00Z", accepted_at: null, expires_at };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OrgMembersPage />
    </QueryClientProvider>,
  );
}

describe("OrgMembersPage invitations", () => {
  beforeEach(() => {
    listMock.mockReset();
    createMock.mockReset();
    createManyMock.mockReset();
    regenerateMock.mockReset();
    resendMock.mockReset();
    revokeMock.mockReset();
    digestMock.mockReset();
    digestMock.mockResolvedValue(digest(false));
  });
  afterEach(() => cleanup());

  it("shows when each invitation expires, with a warning for soon and for already expired", async () => {
    listMock.mockResolvedValue([
      // fixtures sit in the middle of their bucket so a slow run can't tip them over a boundary
      invitation(1, "week@example.com", "pending", inFuture(6 * DAY + 12 * HOUR)),
      invitation(2, "soon@example.com", "pending", inFuture(5 * HOUR + 30 * 60_000)),
      invitation(3, "late@example.com", "expired", inFuture(-3 * DAY)),
      invitation(4, "done@example.com", "accepted", inFuture(-DAY)),
      invitation(5, "stale@example.com", "pending", inFuture(-2 * HOUR)),
    ]);
    renderPage();

    const rowOf = async (email: string) => (await screen.findByText(email)).closest("tr")!;
    expect(await screen.findByRole("columnheader", { name: "Expires" })).toBeInTheDocument();

    expect(within(await rowOf("week@example.com")).getByText("in 7 days")).toBeInTheDocument();
    const soonRow = await rowOf("soon@example.com");
    const soon = within(soonRow).getByText(/in 6 hours/);
    expect(soon.closest("td")).toHaveClass("text-yellow-400");
    expect(within(soonRow).getByText("(expiring soon)")).toBeInTheDocument(); // not colour alone
    const late = within(await rowOf("late@example.com")).getByText(/^expired 3 days ago$/);
    expect(late).toHaveClass("text-destructive");
    expect(within(await rowOf("done@example.com")).getByText("—")).toBeInTheDocument();

    // A pending invitation whose time has passed must not read "pending" next to "expired",
    // nor offer to revoke it.
    const stale = await rowOf("stale@example.com");
    expect(within(stale).getByText("expired")).toBeInTheDocument();
    expect(within(stale).getByText(/^expired 2 hours ago$/)).toBeInTheDocument();
    expect(within(stale).queryByRole("button", { name: /revoke/i })).toBeNull();
    expect(within(await rowOf("week@example.com")).getByRole("button", { name: /revoke/i })).toBeInTheDocument();
  });

  it("lets the admin copy the one-time invite link after creating an invitation", async () => {
    listMock.mockResolvedValue([]);
    createMock.mockResolvedValue({
      invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_abc",
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: "new@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /send invite/i }));

    expect(await screen.findByText("https://clevis.example/invite/tok_abc")).toBeInTheDocument();
    expect(screen.getByText(/shown only now/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy invitation link" }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("https://clevis.example/invite/tok_abc"));
    expect(await screen.findByText("Copied")).toBeInTheDocument();

    // a second invitation brings a new link and a fresh button: it must not still say "Copied"
    createMock.mockResolvedValue({
      invitation: invitation(10, "next@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_def",
    });
    fireEvent.change(screen.getByPlaceholderText("teammate@example.com"), { target: { value: "next@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /send invite/i }));

    expect(await screen.findByText("https://clevis.example/invite/tok_def")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy invitation link" })).toHaveTextContent(/^Copy$/);
  });

  it("regenerates a link and shows the new one for copying", async () => {
    listMock.mockResolvedValue([invitation(1, "bob@example.com", "pending", inFuture(2 * DAY))]);
    regenerateMock.mockResolvedValue({
      invitation: invitation(1, "bob@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_new",
    });
    renderPage();

    const row = (await screen.findByText("bob@example.com")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /regenerate link/i }));

    expect(await screen.findByText("https://clevis.example/invite/tok_new")).toBeInTheDocument();
    expect(regenerateMock).toHaveBeenCalledWith("acme", 1);
    expect(await within(row).findByText(/old one no longer works/i)).toBeInTheDocument();
  });

  it("drops a row's regenerate note once that invitation is revoked", async () => {
    listMock.mockResolvedValue([invitation(1, "bob@example.com", "pending", inFuture(2 * DAY))]);
    regenerateMock.mockResolvedValue({
      invitation: invitation(1, "bob@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_new",
    });
    revokeMock.mockResolvedValue(invitation(1, "bob@example.com", "revoked", inFuture(7 * DAY)));
    renderPage();

    const row = (await screen.findByText("bob@example.com")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: /regenerate link/i }));
    expect(await within(row).findByText(/old one no longer works/i)).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: /revoke/i }));

    await waitFor(() => expect(within(row).queryByText(/old one no longer works/i)).toBeNull());
  });

  it("offers regenerate and resend on an expired invitation, but not revoke", async () => {
    listMock.mockResolvedValue([invitation(2, "late@example.com", "expired", inFuture(-DAY))]);
    renderPage();

    const row = (await screen.findByText("late@example.com")).closest("tr")!;
    expect(within(row).getByRole("button", { name: /regenerate link/i })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /resend/i })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /revoke/i })).toBeNull();
  });

  it("confirms a resend, and shows the server's reason when email is not set up", async () => {
    listMock.mockResolvedValue([
      invitation(1, "ok@example.com", "pending", inFuture(2 * DAY)),
      invitation(2, "nosmtp@example.com", "pending", inFuture(2 * DAY)),
    ]);
    resendMock.mockImplementation((_org: string, id: number) =>
      id === 1 ? Promise.resolve(invitation(1, "ok@example.com", "pending", inFuture(7 * DAY))) : Promise.reject(new Error("Email isn't set up on this instance")),
    );
    renderPage();

    const okRow = (await screen.findByText("ok@example.com")).closest("tr")!;
    const failRow = (await screen.findByText("nosmtp@example.com")).closest("tr")!;
    fireEvent.click(within(okRow).getByRole("button", { name: /resend/i }));
    fireEvent.click(within(failRow).getByRole("button", { name: /resend/i }));

    expect(await within(okRow).findByText(/email sent/i)).toBeInTheDocument();
    expect(await within(failRow).findByRole("alert")).toHaveTextContent("Email isn't set up on this instance");
  });

  it("invites several pasted addresses at once and reports each one", async () => {
    listMock.mockResolvedValue([]);
    createManyMock.mockResolvedValue({
      results: [
        {
          email: "a@example.com",
          status: "created",
          invitation: invitation(5, "a@example.com", "pending", inFuture(7 * DAY)),
          invite_link: "https://clevis.example/invite/tok_a",
        },
        { email: "b@example.com", status: "already_pending", invitation: null, invite_link: null },
        { email: "nope", status: "invalid", invitation: null, invite_link: null },
      ],
    });
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), {
      target: { value: "a@example.com, b@example.com\nnope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send 3 invites" }));

    await waitFor(() => expect(createManyMock).toHaveBeenCalledWith("acme", ["a@example.com", "b@example.com", "nope"], false));
    expect(createMock).not.toHaveBeenCalled();
    expect(await screen.findByText("already has a pending invitation")).toBeInTheDocument();
    expect(screen.getByText("not a valid email address")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy invitation link for a@example.com" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /copy invitation link for b@/i })).toBeNull();
  });

  it("shows the pending label while an invitation is being sent", async () => {
    listMock.mockResolvedValue([]);
    let finish!: (value: unknown) => void;
    createMock.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: "new@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /send invite/i }));

    expect(await screen.findByRole("button", { name: /inviting/i })).toBeDisabled();
    finish({ invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)), invite_link: "https://clevis.example/invite/tok_x" });
    expect(await screen.findByText("https://clevis.example/invite/tok_x")).toBeInTheDocument();
  });

  it("counts a repeated address once, whatever its case", async () => {
    listMock.mockResolvedValue([]);
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), {
      target: { value: "A@example.com, a@EXAMPLE.com\nb@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send 2 invites" }));

    await waitFor(() => expect(createManyMock).toHaveBeenCalledWith("acme", ["A@example.com", "b@example.com"], false));
  });

  it("sends one address on Enter, but with several only on Ctrl+Enter", async () => {
    listMock.mockResolvedValue([]);
    createMock.mockResolvedValue({
      invitation: invitation(9, "one@example.com", "pending", inFuture(7 * DAY)),
      invite_link: "https://clevis.example/invite/tok_one",
    });
    createManyMock.mockResolvedValue({ results: [] });
    renderPage();
    const field = await screen.findByPlaceholderText("teammate@example.com");

    fireEvent.change(field, { target: { value: "one@example.com" } });
    fireEvent.keyDown(field, { key: "a" });
    expect(createMock).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(createMock).toHaveBeenCalledWith("acme", "one@example.com", false));

    fireEvent.change(field, { target: { value: "a@example.com\nb@example.com" } });
    fireEvent.keyDown(field, { key: "Enter" }); // plain Enter just adds a line
    expect(createManyMock).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(createManyMock).toHaveBeenCalledWith("acme", ["a@example.com", "b@example.com"], false));
  });

  it("does not send more than 50 addresses on Ctrl+Enter", async () => {
    listMock.mockResolvedValue([]);
    renderPage();
    const field = await screen.findByPlaceholderText("teammate@example.com");

    fireEvent.change(field, { target: { value: Array.from({ length: 51 }, (_, i) => `u${i}@example.com`).join("\n") } });
    fireEvent.keyDown(field, { key: "Enter", ctrlKey: true });

    expect(createManyMock).not.toHaveBeenCalled();
  });

  it("refuses more than 50 addresses before sending, and says why", async () => {
    listMock.mockResolvedValue([]);
    const emails = (n: number) => Array.from({ length: n }, (_, i) => `u${i}@example.com`).join("\n");
    renderPage();
    const field = await screen.findByPlaceholderText("teammate@example.com");

    fireEvent.change(field, { target: { value: emails(51) } });
    expect(screen.getByRole("alert")).toHaveTextContent("Invite up to 50 addresses at a time (51 entered).");
    expect(screen.getByRole("button", { name: "Send 51 invites" })).toBeDisabled();

    fireEvent.change(field, { target: { value: emails(50) } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Send 50 invites" })).toBeEnabled();
  });

  it("says when an address could not be invited", async () => {
    listMock.mockResolvedValue([]);
    createManyMock.mockResolvedValue({
      results: [
        { email: "a@example.com", status: "failed", invitation: null, invite_link: null },
        { email: "b@example.com", status: "already_pending", invitation: null, invite_link: null },
      ],
    });
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: "a@example.com b@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send 2 invites" }));

    expect(await screen.findByText("couldn't be invited, try again")).toBeInTheDocument();
    // Only the failed address stays in the box, ready to resubmit.
    expect(screen.getByPlaceholderText("teammate@example.com")).toHaveValue("a@example.com");
  });

  describe("links on screen that an action kills", () => {
    const LINK = "https://clevis.example/invite/tok_one";
    const rowOf = (email: string) =>
      waitFor(() => {
        const row = screen.getAllByText(email).map((el) => el.closest("tr")).find(Boolean);
        expect(row).toBeTruthy();
        return row!;
      });

    async function inviteOne() {
      listMock.mockResolvedValue([invitation(9, "one@example.com", "pending", inFuture(7 * DAY))]);
      createMock.mockResolvedValue({ invitation: invitation(9, "one@example.com", "pending", inFuture(7 * DAY)), invite_link: LINK });
      renderPage();
      fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: "one@example.com" } });
      fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
      expect(await screen.findByText(LINK)).toBeInTheDocument();
      return rowOf("one@example.com");
    }

    async function inviteTwoInBulk() {
      listMock.mockResolvedValue([
        invitation(5, "a@example.com", "pending", inFuture(7 * DAY)),
        invitation(6, "c@example.com", "pending", inFuture(7 * DAY)),
      ]);
      createManyMock.mockResolvedValue({
        results: ["a", "c"].map((name, i) => ({
          email: `${name}@example.com`,
          status: "created",
          invitation: invitation(5 + i, `${name}@example.com`, "pending", inFuture(7 * DAY)),
          invite_link: `https://clevis.example/invite/tok_${name}`,
        })),
      });
      renderPage();
      fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: "a@example.com c@example.com" } });
      fireEvent.click(screen.getByRole("button", { name: "Send 2 invites" }));
      expect(await screen.findByRole("button", { name: "Copy invitation link for a@example.com" })).toBeInTheDocument();
      return rowOf("a@example.com");
    }

    it("drops the displayed link once a resend has rotated it", async () => {
      resendMock.mockResolvedValue(invitation(9, "one@example.com", "pending", inFuture(7 * DAY)));
      const row = await inviteOne();

      fireEvent.click(within(row).getByRole("button", { name: /resend/i }));

      await waitFor(() => expect(screen.queryByText(LINK)).toBeNull());
    });

    it("keeps the link when the resend is refused before rotating, drops it when the send fails after", async () => {
      const row = await inviteOne();

      resendMock.mockRejectedValueOnce(Object.assign(new Error("Email isn't set up on this instance"), { status: 409 }));
      fireEvent.click(within(row).getByRole("button", { name: /resend/i }));
      expect(await within(row).findByRole("alert")).toHaveTextContent("Email isn't set up");
      expect(screen.getByText(LINK)).toBeInTheDocument();

      resendMock.mockRejectedValueOnce(Object.assign(new Error("The email couldn't be sent."), { status: 502 }));
      fireEvent.click(within(row).getByRole("button", { name: /resend/i }));
      await waitFor(() => expect(screen.queryByText(LINK)).toBeNull());
      expect(await within(row).findByRole("alert")).toHaveTextContent("The email couldn't be sent.");
    });

    it("regenerating one invitation replaces only its own link, not the rest of a bulk result", async () => {
      regenerateMock.mockResolvedValue({
        invitation: invitation(5, "a@example.com", "pending", inFuture(7 * DAY)),
        invite_link: "https://clevis.example/invite/tok_a2",
      });
      const row = await inviteTwoInBulk();

      fireEvent.click(within(row).getByRole("button", { name: /regenerate link/i }));

      expect(await screen.findByText("https://clevis.example/invite/tok_a2")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Copy invitation link for a@example.com" })).toBeNull();
      expect(screen.getByRole("button", { name: "Copy invitation link for c@example.com" })).toBeInTheDocument();
    });

    it("drops a revoked invitation's copy button from the bulk result", async () => {
      revokeMock.mockResolvedValue(invitation(5, "a@example.com", "revoked", inFuture(7 * DAY)));
      const row = await inviteTwoInBulk();

      fireEvent.click(within(row).getByRole("button", { name: /revoke/i }));

      await waitFor(() => expect(screen.queryByRole("button", { name: "Copy invitation link for a@example.com" })).toBeNull());
      expect(screen.getByRole("button", { name: "Copy invitation link for c@example.com" })).toBeInTheDocument();
    });
  });

  describe("emailing the invitation on create (#953)", () => {
    const smtpOn = () => digestMock.mockResolvedValue(digest(true));
    const typeAddresses = async (text: string) =>
      fireEvent.change(await screen.findByPlaceholderText("teammate@example.com"), { target: { value: text } });

    it("offers no email option when the server has no SMTP, and sends without it", async () => {
      listMock.mockResolvedValue([]);
      createMock.mockResolvedValue({
        invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
        invite_link: "https://clevis.example/invite/tok_abc",
      });
      renderPage();
      await typeAddresses("new@example.com");
      expect(screen.queryByLabelText(/Email the invitation link/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
      await waitFor(() => expect(createMock).toHaveBeenCalledWith("acme", "new@example.com", false));
      expect(await screen.findByText(/no email is sent automatically/i)).toBeInTheDocument();
    });

    it("emails the link by default when SMTP is configured, and says so", async () => {
      smtpOn();
      listMock.mockResolvedValue([]);
      createMock.mockResolvedValue({
        invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
        invite_link: "https://clevis.example/invite/tok_abc",
        email_status: "sent",
      });
      renderPage();
      await typeAddresses("new@example.com");
      expect(await screen.findByLabelText("Email the invitation link to the invitee")).toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
      await waitFor(() => expect(createMock).toHaveBeenCalledWith("acme", "new@example.com", true));
      expect(await screen.findByText(/The invitation was emailed/)).toBeInTheDocument();
      // The link is still there to copy.
      expect(screen.getByText("https://clevis.example/invite/tok_abc")).toBeInTheDocument();
    });

    it("lets the admin untick emailing and sends only the link", async () => {
      smtpOn();
      listMock.mockResolvedValue([]);
      createMock.mockResolvedValue({
        invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
        invite_link: "https://clevis.example/invite/tok_abc",
      });
      renderPage();
      await typeAddresses("new@example.com");
      fireEvent.click(await screen.findByLabelText("Email the invitation link to the invitee"));
      fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
      await waitFor(() => expect(createMock).toHaveBeenCalledWith("acme", "new@example.com", false));
    });

    it("tells the admin to share the link themselves when the email could not be sent", async () => {
      smtpOn();
      listMock.mockResolvedValue([]);
      createMock.mockResolvedValue({
        invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
        invite_link: "https://clevis.example/invite/tok_abc",
        email_status: "failed",
      });
      renderPage();
      await typeAddresses("new@example.com");
      await screen.findByLabelText("Email the invitation link to the invitee");
      fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
      expect(await screen.findByText(/couldn't be sent, so share this link yourself/)).toBeInTheDocument();
    });

    it("explains that the hourly email limit was reached and the link must be shared by hand", async () => {
      smtpOn();
      listMock.mockResolvedValue([]);
      createMock.mockResolvedValue({
        invitation: invitation(9, "new@example.com", "pending", inFuture(7 * DAY)),
        invite_link: "https://clevis.example/invite/tok_abc",
        email_status: "limited",
      });
      renderPage();
      await typeAddresses("new@example.com");
      await screen.findByLabelText("Email the invitation link to the invitee");
      fireEvent.click(screen.getByRole("button", { name: /send invite/i }));
      expect(await screen.findByText(/reached its hourly limit on invitation emails/)).toBeInTheDocument();
      expect(screen.getByText("https://clevis.example/invite/tok_abc")).toBeInTheDocument();
    });

    it("reports delivery per address for a bulk invite", async () => {
      smtpOn();
      listMock.mockResolvedValue([]);
      createManyMock.mockResolvedValue({
        results: [
          { email: "a@example.com", status: "created", invitation: invitation(1, "a@example.com", "pending", inFuture(DAY)), invite_link: "https://clevis.example/invite/a", email_status: "sent" },
          { email: "b@example.com", status: "created", invitation: invitation(2, "b@example.com", "pending", inFuture(DAY)), invite_link: "https://clevis.example/invite/b", email_status: "failed" },
          { email: "c@example.com", status: "created", invitation: invitation(3, "c@example.com", "pending", inFuture(DAY)), invite_link: "https://clevis.example/invite/c", email_status: "limited" },
        ],
      });
      renderPage();
      await typeAddresses("a@example.com, b@example.com, c@example.com");
      expect(await screen.findByLabelText("Email the invitation link to the invitees")).toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: /send 3 invites/i }));
      await waitFor(() =>
        expect(createManyMock).toHaveBeenCalledWith("acme", ["a@example.com", "b@example.com", "c@example.com"], true),
      );
      expect(await screen.findByText(/invited · emailed/)).toBeInTheDocument();
      expect(screen.getByText(/invited · email failed, share the link/)).toBeInTheDocument();
      expect(screen.getByText(/invited · email limit reached, share the link/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Copy invitation link for b@example.com" })).toBeInTheDocument();
    });
  });
});
