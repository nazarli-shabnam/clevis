import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EmptyStateInline, EmptyStateNoAccount, EmptyStatePage } from "@/components/empty-state";

vi.mock("@/lib/use-github-app-slug", () => ({ useGithubAppSlug: () => ({ slug: null, appConfigured: false }) }));

const meMock = vi.fn();
vi.mock("@/lib/api/client", () => ({ api: { auth: { me: (...a: unknown[]) => meMock(...a) } } }));

function renderEmpty(node: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  meMock.mockReset();
  meMock.mockResolvedValue({ id: 1, email: "a@b.c", github_login: "octocat" });
});

afterEach(cleanup);

describe("EmptyStateInline", () => {
  it("renders the noun with no qualifier", () => {
    render(<EmptyStateInline noun="jobs" />);

    expect(screen.getByText("No jobs")).toBeInTheDocument();
  });

  it("renders the qualifier when provided", () => {
    render(<EmptyStateInline noun="repositories" qualifier="acme" />);

    expect(screen.getByText('No repositories matching "acme"')).toBeInTheDocument();
  });
});

describe("EmptyStatePage", () => {
  it("renders the message with no action", () => {
    render(<EmptyStatePage message="No organization configured yet." />);

    expect(screen.getByText("No organization configured yet.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders an action link when provided", () => {
    render(
      <EmptyStatePage
        message="No organization configured yet."
        action={{ href: "/security", label: "Configure" }}
      />,
    );

    const link = screen.getByRole("link", { name: "Configure" });
    expect(link).toHaveAttribute("href", "/security");
  });
});

describe("EmptyStateNoAccount", () => {
  it("renders the default copy when no message override is given", () => {
    renderEmpty(<EmptyStateNoAccount />);
    expect(screen.getByText(/this page has nothing to query/)).toBeInTheDocument();
  });

  it("renders a custom message when one is provided", () => {
    renderEmpty(<EmptyStateNoAccount message="No account selected — type an organization below." />);
    expect(screen.getByText("No account selected — type an organization below.")).toBeInTheDocument();
    expect(screen.queryByText(/this page has nothing to query/)).not.toBeInTheDocument();
  });

  it("renders a real CTA button pointing at Settings, not just a text link", () => {
    renderEmpty(<EmptyStateNoAccount />);
    const link = screen.getByRole("link", { name: "Connect a GitHub account" });
    expect(link).toHaveAttribute("href", "/settings");
  });
});

describe("EmptyStateNoAccount orgOnly", () => {
  it("links to installing the App on an organization instead of reconnecting", () => {
    renderEmpty(<EmptyStateNoAccount orgOnly message="needs an org" />);
    expect(screen.queryByText("Connect a GitHub account")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Install the GitHub App on an organization" })).toHaveAttribute("href", "/settings");
  });
});

describe("EmptyStateNoAccount for an account with no linked GitHub identity (#1281)", () => {
  it("says to link GitHub first, then install the App", async () => {
    meMock.mockResolvedValue({ id: 1, email: "a@b.c", github_login: null });
    renderEmpty(<EmptyStateNoAccount />);

    expect(await screen.findByText("Link your GitHub account first")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Link your GitHub account" })).toHaveAttribute("href", "/settings");
    expect(screen.getByText("Install the GitHub App")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Install the GitHub App on an organization" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connect a GitHub account" })).not.toBeInTheDocument();
  });

  it("keeps the plain button for a linked account, and while the lookup loads or fails", async () => {
    renderEmpty(<EmptyStateNoAccount />);
    expect(await screen.findByRole("link", { name: "Connect a GitHub account" })).toBeInTheDocument();
    expect(screen.queryByText("Link your GitHub account first")).not.toBeInTheDocument();
    cleanup();

    meMock.mockReturnValue(new Promise(() => {}));
    renderEmpty(<EmptyStateNoAccount />);
    expect(screen.getByRole("link", { name: "Connect a GitHub account" })).toBeInTheDocument();
    cleanup();

    meMock.mockRejectedValue(new Error("boom"));
    renderEmpty(<EmptyStateNoAccount />);
    expect(await screen.findByRole("link", { name: "Connect a GitHub account" })).toBeInTheDocument();
  });

  it("does not change the org-only variant", async () => {
    meMock.mockResolvedValue({ id: 1, email: "a@b.c", github_login: null });
    renderEmpty(<EmptyStateNoAccount orgOnly message="needs an org" />);

    expect(screen.getByRole("link", { name: "Install the GitHub App on an organization" })).toBeInTheDocument();
    expect(screen.queryByText("Link your GitHub account first")).not.toBeInTheDocument();
  });
});
