import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const tokensResolveMock = vi.fn();
const workflowsMock = vi.fn();
const runsMock = vi.fn();
const dispatchMock = vi.fn();
const dispatchAllMock = vi.fn();
const reposListMock = vi.fn();
const installationsListMock = vi.fn();
const installationsListForOrgMock = vi.fn();
const orgsMineMock = vi.fn();
const triageGetRepoMock = vi.fn();

// Workspace admin by default (saved tokens are admin-only); member tests flip it.
let mockIsWorkspaceAdmin = true
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { is_workspace_admin: mockIsWorkspaceAdmin } }),
}))

vi.mock("@/lib/api/client", () => ({
  api: {
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: vi.fn(),
    },
    automation: {
      workflows: (...args: unknown[]) => workflowsMock(...args),
      runs: (...args: unknown[]) => runsMock(...args),
      dispatch: (...args: unknown[]) => dispatchMock(...args),
      dispatchAll: (...args: unknown[]) => dispatchAllMock(...args),
    },
    repos: {
      list: (...args: unknown[]) => reposListMock(...args),
    },
    installations: {
      list: (...args: unknown[]) => installationsListMock(...args),
      listForOrg: (...args: unknown[]) => installationsListForOrgMock(...args),
    },
    orgs: { mine: (...args: unknown[]) => orgsMineMock(...args) },
    dependabotTriage: {
      getRepo: (...args: unknown[]) => triageGetRepoMock(...args),
      setRepo: vi.fn(),
      run: vi.fn(),
    },
  },
}));

import AutomationPage from "@/app/automation/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AutomationPage />
    </QueryClientProvider>,
  );
}

const DEMO_REPO = {
  name: "demo",
  full_name: "acme/demo",
  private: false,
  description: null,
  language: null,
  stargazers_count: 0,
  forks_count: 0,
  watchers_count: 0,
  open_issues_count: 0,
  pushed_at: null,
  default_branch: "main",
  html_url: "https://github.com/acme/demo",
};

/** Types the owner, waits for the repo dropdown to populate, then selects `name`. */
async function enterOwnerAndSelectRepo(owner: string, name: string) {
  fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: owner } });
  await waitFor(() => expect(screen.getByRole("option", { name })).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Repository"), { target: { value: name } });
}

describe("AutomationPage", () => {
  beforeEach(() => {
    mockIsWorkspaceAdmin = true;
    tokensResolveMock.mockReset();
    workflowsMock.mockReset();
    runsMock.mockReset();
    dispatchMock.mockReset();
    dispatchAllMock.mockReset();
    reposListMock.mockReset();
    reposListMock.mockResolvedValue({ org: "acme", total: 1, repos: [DEMO_REPO] });
    installationsListMock.mockReset();
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockReset();
    installationsListForOrgMock.mockResolvedValue([]);
    orgsMineMock.mockReset();
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
    triageGetRepoMock.mockReset();
    triageGetRepoMock.mockResolvedValue({ enabled: false, mode: "approve_only", merge_method: "squash" });
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("offers 'Save token for this org' to a workspace admin who typed a token (#660)", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(await screen.findByPlaceholderText(/ghp_/), { target: { value: "ghp_typed" } });

    expect(await screen.findByText("Save token for this org")).toBeInTheDocument();
  });

  it("hides 'Save token for this org' from a non-admin, since saved tokens are admin-only (#660)", async () => {
    mockIsWorkspaceAdmin = false;
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(await screen.findByPlaceholderText(/ghp_/), { target: { value: "ghp_typed" } });

    await screen.findByDisplayValue("ghp_typed");
    expect(screen.queryByText("Save token for this org")).not.toBeInTheDocument();
  });

  it("renders no results panel before any repository is loaded", () => {
    renderPage();
    expect(screen.queryByText("Workflows")).not.toBeInTheDocument();
    expect(workflowsMock).not.toHaveBeenCalled();
  });

  it("gives the owner and token inputs accessible names", () => {
    renderPage();
    expect(screen.getByLabelText("Organization / Owner")).toBe(screen.getByPlaceholderText("e.g. octocat"));
    const token = screen.getByLabelText("GitHub Token");
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveAccessibleDescription(/optional if the GitHub App is connected/);
  });

  it("disables the repository dropdown until an owner is entered", () => {
    renderPage();
    expect(screen.getByLabelText("Repository")).toBeDisabled();
    expect(reposListMock).not.toHaveBeenCalled();
  });

  it("populates the repository dropdown from the entered owner's repo list", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    await waitFor(() => expect(reposListMock).toHaveBeenCalledWith("acme", ""));
    await waitFor(() => expect(screen.getByRole("option", { name: "demo" })).toBeInTheDocument());
    expect(screen.getByLabelText("Repository")).not.toBeDisabled();
  });

  it("does not refetch the repo list (or key it on the token) while a token is typed (#548)", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => expect(reposListMock).toHaveBeenCalledTimes(1));

    const tokenInput = await screen.findByPlaceholderText(/ghp_/);
    for (const partial of ["g", "gh", "ghp", "ghp_a", "ghp_ab"]) {
      fireEvent.change(tokenInput, { target: { value: partial } });
    }
    await new Promise((r) => setTimeout(r, 600));

    expect(reposListMock).toHaveBeenCalledTimes(1);
  });

  it("waits for typing in the owner box to pause before resolving, listing and looking up installations (#548)", async () => {
    renderPage();
    const ownerInput = screen.getByPlaceholderText("e.g. octocat");
    for (const partial of ["a", "ac", "acm", "acme"]) {
      fireEvent.change(ownerInput, { target: { value: partial } });
    }

    await waitFor(() => expect(reposListMock).toHaveBeenCalledWith("acme", ""));
    expect(reposListMock).toHaveBeenCalledTimes(1);
    expect(tokensResolveMock).toHaveBeenCalledTimes(1);
    expect(tokensResolveMock).toHaveBeenCalledWith("acme");
    expect(installationsListForOrgMock).toHaveBeenCalledTimes(1);
  });

  it("clears a selected repository when the owner changes, disabling Load workflows until a new one is picked", async () => {
    // A stale repo name from the old owner must not be submittable against the new owner.
    renderPage();
    await enterOwnerAndSelectRepo("acme", "demo");
    expect(screen.getByLabelText("Repository")).toHaveValue("demo");

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "other-org" } });

    await waitFor(() => expect(screen.getByLabelText("Repository")).toHaveValue(""));
    expect(screen.getByRole("button", { name: "Load workflows" })).toBeDisabled();
  });

  it("shows a 'Failed to load repositories' placeholder when the repo list fetch errors", async () => {
    reposListMock.mockRejectedValue(new Error("GitHub API unreachable"));
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    await waitFor(() => {
      expect(screen.getByRole("option", { name: "Failed to load repositories" })).toBeInTheDocument();
    });
  });

  it("shows a 'No repositories found' placeholder when the owner has zero repos", async () => {
    reposListMock.mockResolvedValue({ org: "acme", total: 0, repos: [] });
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    await waitFor(() => {
      expect(screen.getByRole("option", { name: "No repositories found" })).toBeInTheDocument();
    });
  });

  it("shows the GitHub Token field when no installation covers the entered owner", async () => {
    installationsListMock.mockResolvedValue([]);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => {
      expect(screen.getByText("GitHub Token")).toBeInTheDocument();
    });
  });

  it("hides the GitHub Token field when a personal installation covers the entered owner", async () => {
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "acme", account_type: "Organization", installation_id: 42, created_at: "2026-07-20T00:00:00Z" },
    ]);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => {
      expect(screen.queryByText("GitHub Token")).not.toBeInTheDocument();
    });
  });

  it("hides the GitHub Token field when a personal installation covers the entered owner with trailing whitespace", async () => {
    // "acme " (untrimmed) must still match an "acme" installation and hide the token field.
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "acme", account_type: "Organization", installation_id: 42, created_at: "2026-07-20T00:00:00Z" },
    ]);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme " } });
    await waitFor(() => {
      expect(screen.queryByText("GitHub Token")).not.toBeInTheDocument();
    });
  });

  it("hides the GitHub Token field when an org-level installation covers the entered owner", async () => {
    // installations.list() is personal-only; org installs need the org-scoped endpoint.
    installationsListForOrgMock.mockResolvedValue([
      { id: 2, account_login: "acme", account_type: "Organization", installation_id: 99, created_at: "2026-07-20T00:00:00Z" },
    ]);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => {
      expect(installationsListForOrgMock).toHaveBeenCalledWith("acme");
      expect(screen.queryByText("GitHub Token")).not.toBeInTheDocument();
    });
  });

  it("still shows the GitHub Token field when the org-installation lookup errors (e.g. not a recognized org member)", async () => {
    installationsListForOrgMock.mockRejectedValue(new Error("403"));
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => {
      expect(screen.getByText("GitHub Token")).toBeInTheDocument();
    });
  });

  it("shows a permission-drift notice when the org installation is missing scopes", async () => {
    installationsListForOrgMock.mockResolvedValue([
      {
        id: 2,
        account_login: "acme",
        account_type: "Organization",
        installation_id: 99,
        created_at: "2026-07-20T00:00:00Z",
        permissions_synced_at: "2026-09-01T00:00:00Z",
        blocked_features: [
          { feature: "bulk_branch_protection", label: "Bulk branch-protection apply", missing: { administration: "write" } },
        ],
      },
    ]);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => {
      expect(screen.getByText(/needs extra GitHub access/i)).toBeInTheDocument();
      expect(screen.getByText("Bulk branch-protection apply")).toBeInTheDocument();
    });
  });

  it("loads workflows and run history for the selected owner/repo", async () => {
    tokensResolveMock.mockResolvedValue({ token: "ghp_test" });
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [
        { id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: "completed", last_run_conclusion: "success", last_run_at: "2026-07-20T00:00:00Z" },
      ],
    });
    runsMock.mockResolvedValue({
      repository: "acme/demo",
      runs: [
        { id: 100, name: "CI", status: "completed", conclusion: "success", head_branch: "main", created_at: "2026-07-20T00:00:00Z", duration_ms: 60000 },
      ],
    });

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    // Waiting for the repo dropdown also lets token-resolve settle before "Load workflows".
    await waitFor(() => expect(screen.getByText("saved")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => {
      expect(workflowsMock).toHaveBeenCalledWith("acme", "demo", "ghp_test");
      expect(runsMock).toHaveBeenCalledWith("acme", "demo", "ghp_test");
    });

    await waitFor(() => {
      expect(screen.getAllByText("CI").length).toBeGreaterThan(0);
    });
    expect(screen.getByText("main")).toBeInTheDocument();
  });

  it("arms then confirms a workflow dispatch, calling the API only after the second click", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [{ id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null }],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });
    dispatchMock.mockResolvedValue({ dispatched: true, message: "Workflow dispatched." });

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Dispatch/i }));

    await waitFor(() => {
      expect(screen.getByText("Dispatch workflow")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText("Dispatch workflow"));
    expect(dispatchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Confirm dispatch")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Confirm dispatch"));

    await waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledWith("acme", "demo", 1, { token: "", ref: "main" });
    });
  });

  it("hides the 'Dispatch all' toolbar action when a repo has one workflow", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [{ id: 1, name: "CI", path: "p", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null }],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });

    renderPage();
    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());
    expect(screen.queryByText("Dispatch all")).not.toBeInTheDocument();
  });

  it("arms then confirms 'Dispatch all', showing the result summary", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [
        { id: 1, name: "CI", path: "p", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null },
        { id: 2, name: "Release", path: "p", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null },
      ],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });
    dispatchAllMock.mockResolvedValue({
      ref: "main",
      results: [
        { workflow_id: 1, name: "CI", status: "dispatched", message: null },
        { workflow_id: 2, name: "Release", status: "failed", message: "Resource not accessible by integration" },
      ],
      dispatched_count: 1,
      skipped_count: 0,
      failed_count: 1,
    });

    renderPage();
    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));
    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Dispatch all"));
    expect(dispatchAllMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Confirm — dispatch all"));

    await waitFor(() => expect(dispatchAllMock).toHaveBeenCalledWith("acme", "demo", { token: "", ref: "main" }));
    await waitFor(() => expect(screen.getByText("1 dispatched")).toBeInTheDocument());
    expect(screen.getByText("Release: Resource not accessible by integration")).toBeInTheDocument();

    // Reloading workflows clears the previous bulk-dispatch summary.
    fireEvent.click(screen.getByText("Load workflows"));
    await waitFor(() => expect(screen.queryByText("1 dispatched")).not.toBeInTheDocument());
  });

  it("clears an armed 'Dispatch all' confirmation when the ref is edited", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [
        { id: 1, name: "CI", path: "p", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null },
        { id: 2, name: "Release", path: "p", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null },
      ],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });

    renderPage();
    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));
    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Dispatch all"));
    expect(screen.getByText("Confirm — dispatch all")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Ref for bulk dispatch"), { target: { value: "release" } });
    expect(screen.getByText("Dispatch all")).toBeInTheDocument();
    expect(screen.queryByText("Confirm — dispatch all")).not.toBeInTheDocument();
  });

  it("surfaces an error message when loading workflows fails", async () => {
    workflowsMock.mockRejectedValue(new Error("GitHub API unreachable"));
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => {
      expect(screen.getByText("GitHub API unreachable")).toBeInTheDocument();
    });
  });

  it("shows a loading skeleton, then the empty state, when a repo has no workflows", async () => {
    let resolveWorkflows: (v: unknown) => void = () => {};
    workflowsMock.mockImplementation(
      () => new Promise((res) => { resolveWorkflows = res; }),
    );
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => expect(screen.getByText("Loading…")).toBeInTheDocument());

    resolveWorkflows({ repository: "acme/demo", workflows: [] });

    await waitFor(() => {
      expect(screen.getByText("No workflows found in this repository.")).toBeInTheDocument();
    });
  });

  it("saves a manually entered token and renders failure/pending status icons with fallback labels", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [
        { id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: "in_progress", last_run_conclusion: null, last_run_at: null },
      ],
    });
    runsMock.mockResolvedValue({
      repository: "acme/demo",
      runs: [
        { id: 200, name: null, status: "completed", conclusion: "failure", head_branch: "main", created_at: "2026-07-20T00:00:00Z", duration_ms: null },
        { id: 201, name: "Build", status: "completed", conclusion: "cancelled", head_branch: "dev", created_at: "2026-07-20T00:00:00Z", duration_ms: null },
      ],
    });

    const { api } = await import("@/lib/api/client");
    const upsertMock = api.tokens.upsert as unknown as ReturnType<typeof vi.fn>;
    upsertMock.mockReset();
    upsertMock.mockResolvedValue({});

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.change(screen.getByPlaceholderText(/ghp_/), { target: { value: "ghp_manual123456789012345678901234" } });

    await waitFor(() => expect(screen.getByText("Save token for this org")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Save token for this org"));
    await waitFor(() => expect(upsertMock).toHaveBeenCalledWith("acme", "ghp_manual123456789012345678901234"));

    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => expect(screen.getByText("#200")).toBeInTheDocument());
    expect(screen.getByText("Build")).toBeInTheDocument();
    expect(screen.getByText("in_progress")).toBeInTheDocument();
  });

  it("shows an error when saving the token fails", async () => {
    const { api } = await import("@/lib/api/client");
    const upsertMock = api.tokens.upsert as unknown as ReturnType<typeof vi.fn>;
    upsertMock.mockReset();
    upsertMock.mockRejectedValue(new Error("Invalid token format"));

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.change(screen.getByPlaceholderText(/ghp_/), { target: { value: "ghp_manual123456789012345678901234" } });
    fireEvent.click(await screen.findByText("Save token for this org"));

    await waitFor(() => expect(screen.getByText("Invalid token format")).toBeInTheDocument());
  });

  it("surfaces a dispatch error and lets the user edit the ref before retrying", async () => {
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [{ id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null }],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });
    dispatchMock.mockRejectedValue(new Error("GitHub API error: 422"));

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));

    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/i }));

    const refInput = screen.getByDisplayValue("main");
    fireEvent.change(refInput, { target: { value: "release" } });

    fireEvent.click(screen.getByText("Dispatch workflow"));
    fireEvent.click(screen.getByText("Confirm dispatch"));

    await waitFor(() => {
      expect(screen.getByText("GitHub API error: 422")).toBeInTheDocument();
    });
  });

  it("prefills the dispatch ref from the selected repo's default branch and keeps it editable", async () => {
    reposListMock.mockResolvedValue({
      org: "acme",
      total: 2,
      repos: [{ ...DEMO_REPO, default_branch: "master" }, { ...DEMO_REPO, name: "other", default_branch: "develop" }],
    });
    workflowsMock.mockResolvedValue({
      repository: "acme/demo",
      workflows: [{ id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null }],
    });
    runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });
    dispatchMock.mockResolvedValue({ dispatched: true, message: "Workflow dispatched." });

    renderPage();

    await enterOwnerAndSelectRepo("acme", "demo");
    fireEvent.click(screen.getByText("Load workflows"));
    await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /Dispatch/i }));

    expect(screen.getByDisplayValue("master")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Dispatch workflow"));
    fireEvent.click(screen.getByText("Confirm dispatch"));
    await waitFor(() => {
      expect(dispatchMock).toHaveBeenCalledWith("acme", "demo", 1, { token: "", ref: "master" });
    });
  });


  describe("state left over from a previously loaded repo", () => {
    const TWO_REPOS = {
      org: "acme",
      total: 2,
      repos: [DEMO_REPO, { ...DEMO_REPO, name: "other", default_branch: "develop" }],
    };
    const CI = { id: 1, name: "CI", path: ".github/workflows/ci.yml", state: "active", last_run_status: null, last_run_conclusion: null, last_run_at: null };
    const LINT = { ...CI, id: 2, name: "Lint", path: ".github/workflows/lint.yml" };

    const rowDispatch = (name: string) =>
      screen.getAllByRole("button", { name: /Dispatch/i }).find((b) => b.closest("tr")?.textContent?.includes(name))!;

    async function loadDemoWithCi() {
      reposListMock.mockResolvedValue(TWO_REPOS);
      workflowsMock.mockResolvedValue({ repository: "acme/demo", workflows: [CI, LINT] });
      runsMock.mockResolvedValue({ repository: "acme/demo", runs: [] });
      dispatchMock.mockResolvedValue({ dispatched: true, message: "Workflow dispatched." });
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");
      fireEvent.click(screen.getByText("Load workflows"));
      await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());
    }

    it("drops the loaded workflows and selected workflow when another repo is picked, so dispatch cannot pair them", async () => {
      await loadDemoWithCi();
      fireEvent.click(rowDispatch("CI"));
      expect(screen.getByText("Dispatch workflow")).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "other" } });

      await waitFor(() => expect(screen.queryByText("Dispatch workflow")).not.toBeInTheDocument());
      expect(screen.queryByText("CI")).not.toBeInTheDocument();
      expect(dispatchMock).not.toHaveBeenCalled();
    });

    it("clears the previous workflow's 'Workflow dispatched' result when another workflow is selected", async () => {
      await loadDemoWithCi();
      fireEvent.click(rowDispatch("CI"));
      fireEvent.click(screen.getByText("Dispatch workflow"));
      fireEvent.click(screen.getByText("Confirm dispatch"));
      expect(await screen.findByText(/Workflow dispatched —/)).toBeInTheDocument();

      fireEvent.click(rowDispatch("Lint"));

      await waitFor(() => expect(screen.queryByText(/Workflow dispatched —/)).not.toBeInTheDocument());
    });

    it("does not wipe loaded workflows when only whitespace is added to the owner", async () => {
      await loadDemoWithCi();
      fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme " } });
      await new Promise((r) => setTimeout(r, 50));
      expect(screen.getByText("CI")).toBeInTheDocument();
    });

    it("resets the ref to the default and drops loaded results when the owner changes", async () => {
      await loadDemoWithCi();
      fireEvent.click(rowDispatch("CI"));
      fireEvent.change(screen.getAllByDisplayValue("main")[0], { target: { value: "release" } });
      fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "someone-else" } });

      await waitFor(() => expect(screen.queryByText("CI")).not.toBeInTheDocument());
      expect(screen.queryByDisplayValue("release")).not.toBeInTheDocument();
    });

    it("keeps a ref the user typed when another repo is picked", async () => {
      await loadDemoWithCi();
      fireEvent.click(rowDispatch("CI"));
      fireEvent.change(screen.getAllByDisplayValue("main")[0], { target: { value: "release/1.0" } });

      fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "other" } });
      await waitFor(() => expect(screen.queryByText("CI")).not.toBeInTheDocument());
      fireEvent.click(screen.getByText("Load workflows"));
      await waitFor(() => expect(screen.getByText("CI")).toBeInTheDocument());
      fireEvent.click(rowDispatch("CI"));

      expect(screen.getAllByDisplayValue("release/1.0").length).toBeGreaterThan(0);
    });
  });

  describe("Dependabot auto-triage gating", () => {
    it("shows the triage card, and reads its setting, for an org the caller admins", async () => {
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");

      await waitFor(() => expect(triageGetRepoMock).toHaveBeenCalledWith("acme", "acme", "demo"));
      expect(await screen.findByRole("button", { name: "Save settings" })).toBeInTheDocument();
    });

    it("replaces the card with an admins-only note for a plain member, and never calls the admin-only read", async () => {
      orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");

      expect(await screen.findByText(/available to organization admins/)).toBeInTheDocument();
      expect(screen.getByText(/You're a member of acme, not an admin/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
      expect(triageGetRepoMock).not.toHaveBeenCalled();
    });

    it("holds the card back until the role is known, so a plain member never triggers the admin-only read", async () => {
      let resolveMine!: (v: { org_login: string; role: "member" }[]) => void;
      orgsMineMock.mockReturnValue(new Promise((res) => { resolveMine = res; }));
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");

      expect(await screen.findByText("Checking your access…")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Save settings" })).not.toBeInTheDocument();
      expect(triageGetRepoMock).not.toHaveBeenCalled();

      resolveMine([{ org_login: "acme", role: "member" }]);
      expect(await screen.findByText(/available to organization admins/)).toBeInTheDocument();
      expect(triageGetRepoMock).not.toHaveBeenCalled();
    });

    it("explains that an org Clevis has no admin membership for can't use auto-triage", async () => {
      orgsMineMock.mockResolvedValue([{ org_login: "other", role: "admin" }]);
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");

      expect(await screen.findByText(/needs an organization connected to Clevis where you're an admin/)).toBeInTheDocument();
      expect(triageGetRepoMock).not.toHaveBeenCalled();
    });

    it("matches the owner case-insensitively when deciding the caller is an admin", async () => {
      orgsMineMock.mockResolvedValue([{ org_login: "Acme", role: "admin" }]);
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");

      await waitFor(() => expect(triageGetRepoMock).toHaveBeenCalled());
      expect(screen.queryByText(/available to organization admins/)).not.toBeInTheDocument();
    });

    it("does not guess when the membership lookup fails: the card renders and shows its own state", async () => {
      orgsMineMock.mockRejectedValue(new Error("memberships unavailable"));
      renderPage();
      await enterOwnerAndSelectRepo("acme", "demo");

      await waitFor(() => expect(triageGetRepoMock).toHaveBeenCalled());
      expect(screen.queryByText(/available to organization admins/)).not.toBeInTheDocument();
    });

    it("keeps the (disabled) card, not a note, before any owner is entered", async () => {
      renderPage();

      expect(await screen.findByText("Dependabot auto-triage")).toBeInTheDocument();
      expect(screen.queryByText(/available to organization admins/)).not.toBeInTheDocument();
      expect(triageGetRepoMock).not.toHaveBeenCalled();
    });
  });
});
