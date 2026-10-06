import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const bulkMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: { security: { remediateBulk: (...args: unknown[]) => bulkMock(...args) } },
}));

import { BulkFixPanel, MAX_BATCH } from "@/components/bulk-fix-panel";
import type { RepoSecurityRow } from "@/lib/api/types";

const SS = "repository_secret_scanning_enabled";
const BP = "repository_default_branch_protection_enabled";
const FP = "repository_default_branch_no_force_push";

function row(repo: string, over: Partial<RepoSecurityRow> = {}): RepoSecurityRow {
  return {
    repo,
    branch_protection: true,
    secret_scanning: true,
    dependabot_enabled: true,
    dependabot_critical_count: 0,
    dependabot_high_count: 0,
    code_scanning: true,
    force_push_allowed: false,
    score: 100,
    unknown_dimensions: [],
    alerts_source: "github",
    ...over,
  };
}

const REPOS = [
  row("api", { secret_scanning: false }),
  row("web", { secret_scanning: false, branch_protection: false }),
  row("docs"), // fully compliant
  row("blind", { secret_scanning: false, unknown_dimensions: ["secret_scanning"] }), // can't tell: not "off"
  row("loose", { force_push_allowed: true }),
];

function renderPanel(props: Partial<React.ComponentProps<typeof BulkFixPanel>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onApplied = vi.fn();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <BulkFixPanel owner="acme" repos={REPOS} token="ghp_t" onApplied={onApplied} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, onApplied };
}

const checkbox = (name: string) => screen.getByRole("checkbox", { name });

describe("BulkFixPanel", () => {
  beforeEach(() => bulkMock.mockReset());
  afterEach(() => cleanup());

  it("lists only the repos that fail the chosen check, never ones the token couldn't evaluate", () => {
    renderPanel();

    expect(screen.getByText("2 repos fail it")).toBeInTheDocument();
    expect(checkbox("api")).toBeInTheDocument();
    expect(checkbox("web")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "docs" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "blind" })).toBeNull(); // unknown, not failing

    fireEvent.change(screen.getByLabelText("Fix"), { target: { value: BP } });
    expect(screen.getByText("1 repo fails it")).toBeInTheDocument();
    expect(checkbox("web")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Fix"), { target: { value: FP } });
    expect(checkbox("loose")).toBeInTheDocument();
  });

  it("says so when nothing fails the chosen check", () => {
    renderPanel({ repos: [row("docs")] });
    expect(screen.getByText("no repos in the matrix fail this check")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /preview changes/i })).toBeDisabled();
  });

  it("needs a selection before it can preview, and 'select all' toggles every candidate", () => {
    renderPanel();
    const preview = screen.getByRole("button", { name: /preview changes/i });
    expect(preview).toBeDisabled();

    fireEvent.click(screen.getByLabelText("Select all (2)"));
    expect(checkbox("api")).toBeChecked();
    expect(checkbox("web")).toBeChecked();
    expect(screen.getByRole("button", { name: "Preview changes (2)" })).toBeEnabled();

    fireEvent.click(screen.getByLabelText("Select all (2)"));
    expect(checkbox("api")).not.toBeChecked();
    expect(screen.getByRole("button", { name: /preview changes/i })).toBeDisabled();
  });

  it("previews with a dry run of exactly the selected repos, writing nothing", async () => {
    bulkMock.mockResolvedValue({
      check_id: SS,
      dry_run: true,
      items: [
        { repo: "api", status: "would_change", detail: "Will enable secret scanning." },
        { repo: "web", status: "unchanged", detail: "Secret scanning is already enabled." },
      ],
    });
    renderPanel();

    fireEvent.click(checkbox("api"));
    fireEvent.click(checkbox("web"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (2)" }));

    expect(await screen.findByText("Will enable secret scanning.")).toBeInTheDocument();
    expect(bulkMock).toHaveBeenCalledTimes(1);
    expect(bulkMock).toHaveBeenCalledWith("acme", { check_id: SS, repos: ["api", "web"], dry_run: true, token: "ghp_t" });
    expect(screen.getByText("will change")).toBeInTheDocument();
    expect(screen.getByText("no change needed")).toBeInTheDocument();
  });

  it("applies only the repos the preview said would change, after a second confirming click", async () => {
    bulkMock
      .mockResolvedValueOnce({
        check_id: SS,
        dry_run: true,
        items: [
          { repo: "api", status: "would_change", detail: "Will enable secret scanning." },
          { repo: "web", status: "unchanged", detail: "Secret scanning is already enabled." },
        ],
      })
      .mockResolvedValueOnce({
        check_id: SS,
        dry_run: false,
        items: [{ repo: "api", status: "applied", detail: "Will enable secret scanning." }],
      });
    const { onApplied } = renderPanel();
    fireEvent.click(screen.getByLabelText("Select all (2)"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (2)" }));

    fireEvent.click(await screen.findByRole("button", { name: "Apply to 1 repo" }));
    expect(bulkMock).toHaveBeenCalledTimes(1); // armed, not applied yet
    fireEvent.click(screen.getByRole("button", { name: "Confirm — change 1 repo" }));

    expect(await screen.findByText("applied")).toBeInTheDocument();
    expect(bulkMock).toHaveBeenLastCalledWith("acme", { check_id: SS, repos: ["api"], dry_run: false, token: "ghp_t" });
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Re-run the scan to confirm the results.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /apply to|confirm/i })).toBeNull();
  });

  it("can cancel the confirmation without applying", async () => {
    bulkMock.mockResolvedValue({ check_id: SS, dry_run: true, items: [{ repo: "api", status: "would_change", detail: "d" }] });
    renderPanel();
    fireEvent.click(checkbox("api"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (1)" }));

    fireEvent.click(await screen.findByRole("button", { name: "Apply to 1 repo" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Apply to 1 repo" })).toBeInTheDocument();
    expect(bulkMock).toHaveBeenCalledTimes(1);
  });

  it("offers nothing to apply when every selected repo is already fine", async () => {
    bulkMock.mockResolvedValue({ check_id: SS, dry_run: true, items: [{ repo: "api", status: "unchanged", detail: "Already on." }] });
    renderPanel();
    fireEvent.click(checkbox("api"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (1)" }));

    expect(await screen.findByText("Nothing to change for the selected repositories.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /apply to/i })).toBeNull();
  });

  it("shows a failed repo's reason and keeps the rest of the batch visible", async () => {
    bulkMock.mockResolvedValue({
      check_id: SS,
      dry_run: true,
      items: [
        { repo: "api", status: "would_change", detail: "Will enable secret scanning." },
        { repo: "web", status: "failed", detail: "GitHub API error: 404" },
      ],
    });
    renderPanel();
    fireEvent.click(screen.getByLabelText("Select all (2)"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (2)" }));

    const failed = (await screen.findByText("GitHub API error: 404")).closest("li")!;
    expect(within(failed).getByText("failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply to 1 repo" })).toBeInTheDocument();
  });

  it("selects at most the batch cap, and says so when more repos fail", () => {
    const many = Array.from({ length: MAX_BATCH + 20 }, (_, i) => row(`repo-${String(i).padStart(3, "0")}`, { secret_scanning: false }));
    renderPanel({ repos: many });

    expect(screen.getByText(`${MAX_BATCH + 20} repos fail it`)).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`limited to ${MAX_BATCH} repositories`))).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(`Select all (${MAX_BATCH})`));
    expect(screen.getByRole("button", { name: `Preview changes (${MAX_BATCH})` })).toBeEnabled();
    // everything past the cap is locked until something is unticked
    expect(checkbox(`repo-${String(MAX_BATCH).padStart(3, "0")}`)).toBeDisabled();
    expect(checkbox("repo-000")).toBeEnabled();

    fireEvent.click(checkbox("repo-000"));
    expect(checkbox(`repo-${String(MAX_BATCH).padStart(3, "0")}`)).toBeEnabled();
  });

  it("shows the permission hint once when every repo was refused, next to the per-repo results", async () => {
    bulkMock.mockResolvedValueOnce({
      check_id: SS,
      dry_run: true,
      items: [
        { repo: "api", status: "failed", detail: "GitHub API error: 403" },
        { repo: "web", status: "failed", detail: "GitHub API error: 403" },
      ],
      hint: "GitHub returned 403 for every repo. Administration needed.",
    });
    renderPanel();
    fireEvent.click(screen.getByLabelText("Select all (2)"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (2)" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Administration needed.");
    expect(screen.getAllByText("GitHub API error: 403")).toHaveLength(2);
  });

  it("surfaces an API error (e.g. the missing-permission hint) instead of a result", async () => {
    bulkMock.mockRejectedValueOnce(new Error("GitHub returned 403 for every repo. Administration needed."));
    renderPanel();
    fireEvent.click(checkbox("api"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (1)" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Administration needed.");
  });

  it("surfaces a failed apply", async () => {
    bulkMock
      .mockResolvedValueOnce({ check_id: SS, dry_run: true, items: [{ repo: "api", status: "would_change", detail: "d" }] })
      .mockRejectedValueOnce(new Error("GitHub API unreachable"));
    renderPanel();
    fireEvent.click(checkbox("api"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (1)" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply to 1 repo" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm — change 1 repo" }));

    expect(await screen.findByText("GitHub API unreachable")).toBeInTheDocument();
  });

  it("drops the preview and the confirmation when the selection or the check changes", async () => {
    bulkMock.mockResolvedValue({ check_id: SS, dry_run: true, items: [{ repo: "api", status: "would_change", detail: "d" }] });
    renderPanel();
    fireEvent.click(checkbox("api"));
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (1)" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply to 1 repo" }));
    expect(screen.getByRole("button", { name: "Confirm — change 1 repo" })).toBeInTheDocument();

    // changing the selection must not leave an armed button that would apply a different set
    fireEvent.click(checkbox("web"));
    await waitFor(() => expect(screen.queryByRole("button", { name: /confirm — change/i })).toBeNull());
    expect(screen.queryByText("d")).toBeNull();
    expect(bulkMock).toHaveBeenCalledTimes(1);
  });

  it("never acts on a repo that has dropped out of the matrix since it was ticked", async () => {
    bulkMock.mockResolvedValue({ check_id: SS, dry_run: true, items: [{ repo: "web", status: "would_change", detail: "d" }] });
    const { rerender } = renderPanel();
    fireEvent.click(screen.getByLabelText("Select all (2)"));

    // a fresh scan: api now passes
    const queryClient = new QueryClient();
    rerender(
      <QueryClientProvider client={queryClient}>
        <BulkFixPanel owner="acme" repos={[row("api"), REPOS[1]]} token="ghp_t" />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Preview changes (1)" }));

    await waitFor(() => expect(bulkMock).toHaveBeenCalled());
    expect(bulkMock).toHaveBeenCalledWith("acme", expect.objectContaining({ repos: ["web"] }));
  });
});
