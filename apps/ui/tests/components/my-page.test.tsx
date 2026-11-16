import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const myPrsMock = vi.fn();
const myReviewsMock = vi.fn();
const myIssuesMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: { resolve: (...a: unknown[]) => tokensResolveMock(...a) },
    analytics: {
      myPrs: (...a: unknown[]) => myPrsMock(...a),
      myReviews: (...a: unknown[]) => myReviewsMock(...a),
      myIssues: (...a: unknown[]) => myIssuesMock(...a),
    },
  },
}));

const replace = vi.fn();
let tabParam: string | null = null;
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(tabParam ? `tab=${tabParam}` : ""),
}));

import MyWorkPage from "@/app/my/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  // A fresh element each time: re-rendering the very same element object would bail out before the
  // (mocked) search params are read again.
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <MyWorkPage />
    </QueryClientProvider>
  );
  const view = render(tree());
  return { ...view, rerenderSamePage: () => view.rerender(tree()) };
}

const ROW = {
  number: 12,
  title: "Fix bug",
  repository: "acme/api",
  html_url: "https://github.com/acme/api/pull/12",
  updated_at: "2026-07-20T00:00:00Z",
};

describe("MyWorkPage (tabbed /my)", () => {
  beforeEach(() => {
    tokensResolveMock.mockReset();
    myPrsMock.mockReset();
    myReviewsMock.mockReset();
    myIssuesMock.mockReset();
    replace.mockReset();
    tabParam = null;
    localStorage.clear();
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows the no-account empty state when no scope is selected", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/No account selected/)).toBeInTheDocument());
    expect(myPrsMock).not.toHaveBeenCalled();
  });

  it("defaults to the PRs tab and queries my-prs", async () => {
    localStorage.setItem("default_org", "acme");
    myPrsMock.mockResolvedValue({ items: [ROW], total_count: 1, page: 1, per_page: 25 });

    renderPage();

    await waitFor(() => expect(screen.getByText("Fix bug")).toBeInTheDocument());
    expect(myPrsMock).toHaveBeenCalledWith("acme", 1, 25);
    expect(myReviewsMock).not.toHaveBeenCalled();
  });

  it("deep-links to the reviews tab via ?tab=reviews", async () => {
    tabParam = "reviews";
    localStorage.setItem("default_org", "acme");
    myReviewsMock.mockResolvedValue({ items: [ROW], total_count: 1, page: 1, per_page: 25 });

    renderPage();

    await waitFor(() => expect(myReviewsMock).toHaveBeenCalledWith("acme", 1, 25));
    expect(screen.getByRole("button", { name: "My Reviews", pressed: true })).toBeInTheDocument();
    expect(myPrsMock).not.toHaveBeenCalled();
  });

  it("routes to ?tab=issues when the Issues tab is clicked", async () => {
    localStorage.setItem("default_org", "acme");
    myPrsMock.mockResolvedValue({ items: [], total_count: 0, page: 1, per_page: 25 });

    renderPage();
    await waitFor(() => expect(myPrsMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "My Issues" }));
    expect(replace).toHaveBeenCalledWith("?tab=issues", { scroll: false });
  });

  it("drops the tab param when returning to the PRs tab", async () => {
    tabParam = "issues";
    localStorage.setItem("default_org", "acme");
    myIssuesMock.mockResolvedValue({ items: [], total_count: 0, page: 1, per_page: 25 });

    renderPage();
    await waitFor(() => expect(myIssuesMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "My PRs" }));
    expect(replace).toHaveBeenCalledWith("?", { scroll: false });
  });
  it("keeps the current page on screen while the next one loads, and never requests a stale page after a tab switch (#1091)", async () => {
    tabParam = "reviews";
    localStorage.setItem("default_org", "acme");
    const page2 = { items: [{ ...ROW, number: 99, title: "Second page" }], total_count: 60, page: 2, per_page: 25 };
    let resolvePage2: (v: unknown) => void = () => {};
    myReviewsMock.mockImplementation((_org: string, page: number) =>
      page === 1
        ? Promise.resolve({ items: [ROW], total_count: 60, page: 1, per_page: 25 })
        : new Promise((resolve) => { resolvePage2 = resolve; }),
    );
    myPrsMock.mockResolvedValue({ items: [{ ...ROW, number: 7, title: "A PR" }], total_count: 1, page: 1, per_page: 25 });

    const { rerenderSamePage } = renderPage();
    expect(await screen.findByText("Fix bug")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(myReviewsMock).toHaveBeenCalledWith("acme", 2, 25));
    // The list is not replaced by "Loading..." while page 2 is in flight.
    expect(screen.getByText("Fix bug")).toBeInTheDocument();
    resolvePage2(page2);
    expect(await screen.findByText("Second page")).toBeInTheDocument();

    // Switching tab starts that list at page 1: the PRs query is never asked for page 2, and the reviews
    // rows are not shown under the PRs tab while it loads.
    tabParam = null;
    rerenderSamePage();
    await waitFor(() => expect(myPrsMock).toHaveBeenCalledWith("acme", 1, 25));
    expect(myPrsMock).not.toHaveBeenCalledWith("acme", 2, 25);
    expect(await screen.findByText("A PR")).toBeInTheDocument();
    expect(screen.queryByText("Second page")).not.toBeInTheDocument();
  });
});
