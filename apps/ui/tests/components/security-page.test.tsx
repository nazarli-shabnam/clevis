import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSyncExternalStore } from "react";

const tokensResolveMock = vi.fn();
const tokensUpsertMock = vi.fn();
const analyticsOverviewMock = vi.fn();
const analyticsHistoryMock = vi.fn();
const analyticsExportMock = vi.fn();
const analyticsChangesMock = vi.fn();
const analyticsLatestScanMock = vi.fn();
const downloadTextFileMock = vi.fn();

vi.mock("@/lib/download", () => ({
  downloadTextFile: (...args: unknown[]) => downloadTextFileMock(...args),
}));
const securityMatrixMock = vi.fn();
const secretScanningMock = vi.fn();
const remediateMock = vi.fn();
const installationsListMock = vi.fn();
const installationsListForOrgMock = vi.fn();

// Minimal reactive store standing in for searchParams so router.replace() re-renders.
let mockSearchParams = new URLSearchParams();
const searchParamsListeners = new Set<() => void>();
const routerReplaceMock = vi.fn((url: string) => {
  mockSearchParams = new URLSearchParams(url.split("?")[1] ?? "");
  searchParamsListeners.forEach((listener) => listener());
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: routerReplaceMock }),
  useSearchParams: () =>
    useSyncExternalStore(
      (listener) => {
        searchParamsListeners.add(listener);
        return () => searchParamsListeners.delete(listener);
      },
      () => mockSearchParams,
    ),
}));

const orgsMineMock = vi.fn().mockResolvedValue([]);
// Workspace admin by default (saved tokens are admin-only); member tests flip it.
let mockIsWorkspaceAdmin = true
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: { is_workspace_admin: mockIsWorkspaceAdmin } }),
}))

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: (...args: unknown[]) => orgsMineMock(...args) },
    tokens: {
      resolve: (...args: unknown[]) => tokensResolveMock(...args),
      upsert: (...args: unknown[]) => tokensUpsertMock(...args),
    },
    analytics: {
      overview: (...args: unknown[]) => analyticsOverviewMock(...args),
      history: (...args: unknown[]) => analyticsHistoryMock(...args),
      exportHistory: (...args: unknown[]) => analyticsExportMock(...args),
      changes: (...args: unknown[]) => analyticsChangesMock(...args),
      latestScan: (...args: unknown[]) => analyticsLatestScanMock(...args),
    },
    security: {
      matrix: (...args: unknown[]) => securityMatrixMock(...args),
      secretScanning: (...args: unknown[]) => secretScanningMock(...args),
      remediate: (...args: unknown[]) => remediateMock(...args),
    },
    installations: {
      list: (...args: unknown[]) => installationsListMock(...args),
      listForOrg: (...args: unknown[]) => installationsListForOrgMock(...args),
    },
  },
}));

// Recharts can't be clicked meaningfully in jsdom; expose the point-click hook as a button instead.
vi.mock("@/components/charts/area-time-chart", () => ({
  AreaTimeChart: ({ onPointClick }: { onPointClick?: (i: number) => void }) =>
    onPointClick ? (
      <button type="button" onClick={() => onPointClick(0)}>
        trend-point
      </button>
    ) : null,
}));

import SecurityPage from "@/app/security/page";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SecurityPage />
    </QueryClientProvider>,
  );
}

describe("SecurityPage", () => {
  beforeEach(() => {
    mockIsWorkspaceAdmin = true;
    tokensResolveMock.mockReset();
    tokensUpsertMock.mockReset();
    analyticsOverviewMock.mockReset();
    analyticsHistoryMock.mockReset();
    securityMatrixMock.mockReset();
    secretScanningMock.mockReset();
    remediateMock.mockReset();
    routerReplaceMock.mockClear();
    analyticsExportMock.mockReset();
    downloadTextFileMock.mockReset();
    tokensResolveMock.mockRejectedValue(new Error("no saved token"));
    analyticsHistoryMock.mockResolvedValue([]);
    analyticsChangesMock.mockReset();
    analyticsLatestScanMock.mockReset();
    analyticsLatestScanMock.mockRejectedValue(new Error("No scan yet"));
    analyticsChangesMock.mockResolvedValue({ org: "acme", has_previous: false, scanned_at: null, score: null, comparable: true, changes: [] });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockResolvedValue({ repository: "acme/demo", alerts: [] });
    installationsListMock.mockReset();
    installationsListMock.mockResolvedValue([]);
    installationsListForOrgMock.mockReset();
    installationsListForOrgMock.mockResolvedValue([]);
    mockSearchParams = new URLSearchParams();
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("looks the org up once after typing pauses, not once per keystroke", async () => {
    renderPage();
    const box = screen.getByLabelText("Organization");
    for (const value of ["a", "ac", "acm", "acme"]) fireEvent.change(box, { target: { value } });

    await waitFor(() => expect(tokensResolveMock).toHaveBeenCalledTimes(1));
    expect(tokensResolveMock).toHaveBeenCalledWith("acme");
    await waitFor(() => expect(installationsListForOrgMock).toHaveBeenCalledWith("acme"));
    expect(installationsListForOrgMock).toHaveBeenCalledTimes(1);
    expect(analyticsHistoryMock).toHaveBeenCalledTimes(1);
    expect(analyticsHistoryMock).toHaveBeenCalledWith("acme");
  });

  it("shows the changed-since-last-scan panel for the active org scope", async () => {
    localStorage.setItem("active_scope", JSON.stringify({ kind: "org", login: "acme" }));
    renderPage();
    await waitFor(() => expect(screen.getByText("Changed since the last scan")).toBeInTheDocument());
    expect(analyticsChangesMock).toHaveBeenCalledWith("acme");
  });

  it("does not show the changed-since-last-scan panel without an org scope", async () => {
    renderPage();
    await waitFor(() => expect(analyticsHistoryMock).not.toHaveBeenCalled());
    expect(screen.queryByText("Changed since the last scan")).not.toBeInTheDocument();
    expect(analyticsChangesMock).not.toHaveBeenCalled();
  });

  it("hides the GitHub Token field when an installation covers the entered org", async () => {
    installationsListMock.mockResolvedValue([
      { id: 1, account_login: "acme", account_type: "Organization", installation_id: 42, created_at: "2026-07-20T00:00:00Z" },
    ]);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => {
      expect(screen.queryByText("GitHub Token")).not.toBeInTheDocument();
    });
  });

  it("hides the GitHub Token field when an installation covers the entered org with trailing whitespace", async () => {
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

  it("hides the GitHub Token field when an org-level installation covers the entered org", async () => {
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

  it("offers 'Save token for this org' to a workspace admin who typed a token (#660)", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(await screen.findByPlaceholderText(/leave blank to use the connected GitHub App/i), { target: { value: "ghp_typed" } });

    expect(await screen.findByText("Save token for this org")).toBeInTheDocument();
  });

  it("hides 'Save token for this org' from a non-admin, since saved tokens are admin-only (#660)", async () => {
    mockIsWorkspaceAdmin = false;
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(await screen.findByPlaceholderText(/leave blank to use the connected GitHub App/i), { target: { value: "ghp_typed" } });

    await screen.findByDisplayValue("ghp_typed");
    expect(screen.queryByText("Save token for this org")).not.toBeInTheDocument();
  });

  it("shows n/a, not a perfect score, for a scan that scored no checks", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: null, total_checks: 0, failed_checks: 0, repo_count: 0,
      checks: [{
        id: "repository_license_present", title: "License present", severity: "low", remediation: "",
        status: "pass", scored: false, informational: true, value: null,
      }],
    });
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    expect(await screen.findByLabelText("Security score: not available")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
    expect(screen.getByText("No checks were scored")).toBeInTheDocument();
  });

  it("allows running a scan with no token entered (GitHub App fallback)", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 100,
      total_checks: 0,
      failed_checks: 0,
      repo_count: 0,
      checks: [],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());

    fireEvent.click(scanButton);

    await waitFor(() => expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", ""));
  });

  it("counts errored checks in the donut so its total matches the gauge's check count (#666)", async () => {
    const check = (id: string, status: string, severity = "high") => ({
      id, title: id, severity, remediation: "", status, value: { type: "boolean", enabled: false },
    });
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 33, total_checks: 3, failed_checks: 2, repo_count: 1,
      checks: [check("a", "pass"), check("b", "fail"), check("c", "error")],
    });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    // The donut's centre is the sum of its slices: pass + fail + error = all 3 checks.
    await waitFor(() => expect(screen.getByText("checks").previousElementSibling).toHaveTextContent("3"));
  });

  const STORED_SCAN = {
    owner: "acme", score: 67, total_checks: 3, failed_checks: 1,
    scanned_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    checks: [{ id: "c1", title: "Stored failing check", severity: "high", remediation: "", status: "fail", value: { type: "boolean", enabled: false } }],
  };

  it("shows the org's last stored scan on load, with when it ran and a Rescan button", async () => {
    analyticsLatestScanMock.mockResolvedValue(STORED_SCAN);
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    expect(await screen.findByText("Stored failing check")).toBeInTheDocument();
    expect(screen.getByText(/Last scanned 3 hours ago/)).toBeInTheDocument();
    expect(analyticsLatestScanMock).toHaveBeenCalledWith("acme");
    expect(analyticsOverviewMock).not.toHaveBeenCalled(); // no GitHub calls just to look
    expect(screen.getByRole("button", { name: "Rescan" })).toBeInTheDocument();
  });

  it("replaces the stored results with the fresh ones when a rescan finishes", async () => {
    analyticsLatestScanMock.mockResolvedValue(STORED_SCAN);
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 1, failed_checks: 0, repo_count: 1,
      checks: [{ id: "c2", title: "Fresh passing check", severity: "low", remediation: "", status: "pass", value: null }],
    });
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await screen.findByText("Stored failing check");

    fireEvent.click(screen.getByRole("button", { name: "Rescan" }));

    expect(await screen.findByText("Fresh passing check")).toBeInTheDocument();
    expect(screen.queryByText("Stored failing check")).not.toBeInTheDocument();
    expect(screen.queryByText(/Last scanned/)).not.toBeInTheDocument();
  });

  it("keeps the stored results visible while a rescan runs", async () => {
    analyticsLatestScanMock.mockResolvedValue(STORED_SCAN);
    analyticsOverviewMock.mockReturnValue(new Promise(() => {}));
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await screen.findByText("Stored failing check");

    fireEvent.click(screen.getByRole("button", { name: "Rescan" }));

    expect(await screen.findByText(/Rescanning…/)).toBeInTheDocument();
    expect(screen.getByText("Stored failing check")).toBeInTheDocument();
  });

  it("shows no results area, and still offers Run scan, when nothing is stored", async () => {
    renderPage(); // latestScan rejects: never scanned, or not an org the caller belongs to
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await waitFor(() => expect(analyticsLatestScanMock).toHaveBeenCalled());

    expect(screen.queryByText(/Results —/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run scan" })).toBeInTheDocument();
  });

  it("drops the previous owner's scan results and stops querying for them when the owner changes (#549)", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 50, total_checks: 1, failed_checks: 1, repo_count: 1,
      checks: [{ id: "c1", title: "Acme-only check", severity: "high", remediation: "", status: "fail", value: { type: "boolean", enabled: false } }],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [{
        repo: "acme-repo", branch_protection: true, secret_scanning: true, dependabot_enabled: true,
        dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false,
        score: 100, unknown_dimensions: [], alerts_source: "github",
      }],
      summary: { fully_compliant_count: 1, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });

    renderPage();
    const ownerInput = screen.getByPlaceholderText("e.g. octocat");
    fireEvent.change(ownerInput, { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);
    expect(await screen.findByText("Acme-only check")).toBeInTheDocument();
    await waitFor(() => expect(secretScanningMock).toHaveBeenCalledWith("acme", "acme-repo", ""));

    secretScanningMock.mockClear();
    fireEvent.change(ownerInput, { target: { value: "beta" } });

    await waitFor(() => expect(screen.queryByText("Acme-only check")).not.toBeInTheDocument());
    expect(secretScanningMock).not.toHaveBeenCalled();
  });

  it("says when the matrix covers only some of the org's repositories (#897)", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 150, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [{
        repo: "acme-repo", branch_protection: true, secret_scanning: true, dependabot_enabled: true,
        dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false,
        score: 100, unknown_dimensions: [], alerts_source: "github",
      }],
      summary: { fully_compliant_count: 1, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
      repos_total: 150,
      repos_scanned: 20,
    });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    expect(await screen.findByText(/Showing 20 of 150 repositories/)).toBeInTheDocument();
  });

  it("shows a scan failure for the owner it was run for, and drops it once the owner changes (#549)", async () => {
    analyticsOverviewMock.mockRejectedValue(new Error("scan exploded"));

    renderPage();
    const ownerInput = screen.getByPlaceholderText("e.g. octocat");
    fireEvent.change(ownerInput, { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    expect(await screen.findByTestId("scan-error")).toHaveTextContent("scan exploded");

    fireEvent.change(ownerInput, { target: { value: "beta" } });
    await waitFor(() => expect(screen.queryByTestId("scan-error")).not.toBeInTheDocument());
  });

  it("shows the results card with a pending state while a scan is running (#549)", async () => {
    analyticsOverviewMock.mockReturnValue(new Promise(() => {})); // never settles

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    await waitFor(() => expect(screen.getByRole("button", { name: /scanning/i })).toBeInTheDocument());
    expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", "");
  });

  it("runs a scan on Enter in the organization field with no token entered", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 100,
      total_checks: 0,
      failed_checks: 0,
      repo_count: 0,
      checks: [],
    });

    renderPage();

    const orgInput = screen.getByPlaceholderText("e.g. octocat");
    fireEvent.change(orgInput, { target: { value: "acme" } });
    await waitFor(() => expect(screen.getByRole("button", { name: /run scan/i })).not.toBeDisabled());

    fireEvent.keyDown(orgInput, { key: "Enter" });
    await waitFor(() => expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", ""));
  });

  it("runs a scan on Enter in the token field", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 100,
      total_checks: 0,
      failed_checks: 0,
      repo_count: 0,
      checks: [],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const tokenInput = screen.getByPlaceholderText(/leave blank to use the connected GitHub App/i);
    fireEvent.change(tokenInput, { target: { value: "ghp_test" } });

    fireEvent.keyDown(tokenInput, { key: "Enter" });
    await waitFor(() => expect(analyticsOverviewMock).toHaveBeenCalledWith("acme", "ghp_test"));
  });

  it("shows an error when saving the token fails", async () => {
    tokensUpsertMock.mockRejectedValue(new Error("Invalid token format"));

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.change(screen.getByPlaceholderText(/leave blank to use the connected GitHub App/i), {
      target: { value: "ghp_test" },
    });
    fireEvent.click(await screen.findByText("Save token for this org"));

    await waitFor(() => expect(screen.getByTestId("save-token-error")).toHaveTextContent("Invalid token format"));
  });

  it("renders the score trend chart once 2+ history points are available", async () => {
    analyticsHistoryMock.mockResolvedValue([
      { id: 2, owner: "acme", score: 90, total_checks: 3, failed_checks: 0, created_at: "2026-07-17T00:00:00Z" },
      { id: 1, owner: "acme", score: 70, total_checks: 3, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ]);
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 90,
      total_checks: 0,
      failed_checks: 0,
      repo_count: 0,
      checks: [],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    await waitFor(() => expect(screen.getByText(/Score trend \(last 2 scans\)/)).toBeInTheDocument());
  });

  it("opens a stored scan from the history list and from a score-trend point, keeping it in the URL", async () => {
    const history = [
      { id: 2, owner: "acme", score: 90, total_checks: 3, failed_checks: 0, created_at: "2026-07-17T00:00:00Z" },
      { id: 1, owner: "acme", score: 70, total_checks: 3, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ];
    analyticsHistoryMock.mockResolvedValue(history);
    analyticsExportMock.mockResolvedValue({ truncated: false, row_count: 2, entries: history.map((h) => ({ ...h, checks: [] })) });
    renderPage();
    await runScan();

    const views = await screen.findAllByRole("button", { name: /View scan from/ });
    fireEvent.click(views[0]);
    expect(routerReplaceMock).toHaveBeenLastCalledWith("?scan=2&owner=acme", { scroll: false });

    fireEvent.click(await screen.findByRole("button", { name: "trend-point" }));
    expect(routerReplaceMock).toHaveBeenLastCalledWith("?scan=1&owner=acme", { scroll: false });
  });

  it("follows a scan link's owner when client-side navigation changes it, but not unrelated URL changes", async () => {
    renderPage();
    const box = screen.getByPlaceholderText("e.g. octocat") as HTMLInputElement;
    const navigate = (qs: string) =>
      act(() => {
        mockSearchParams = new URLSearchParams(qs);
        searchParamsListeners.forEach((l) => l());
      });

    navigate("scan=42&owner=beta");
    await waitFor(() => expect(box.value).toBe("beta"));

    fireEvent.change(box, { target: { value: "manual" } });
    navigate("scan=42&owner=beta&tab=fail");
    expect(box.value).toBe("manual");

    navigate("scan=43&owner=gamma");
    await waitFor(() => expect(box.value).toBe("gamma"));
  });

  async function runScan() {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 90, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);
  }

  it("shows an error with Retry when the scan history fails to load, instead of silently dropping it", async () => {
    analyticsHistoryMock.mockRejectedValue(new Error("history unavailable"));
    renderPage();
    await runScan();

    // Once next to the export controls, once in place of the Remediation Trend chart.
    await waitFor(() =>
      expect(screen.getAllByText(/Couldn't load scan history: history unavailable/)).toHaveLength(2),
    );
    expect(screen.queryByText("Run more scans to see a trend")).not.toBeInTheDocument();

    analyticsHistoryMock.mockResolvedValue([
      { id: 2, owner: "acme", score: 90, total_checks: 3, failed_checks: 0, created_at: "2026-07-17T00:00:00Z" },
      { id: 1, owner: "acme", score: 70, total_checks: 3, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ]);
    fireEvent.click(screen.getAllByRole("button", { name: "Retry" })[0]);

    await waitFor(() => expect(screen.queryByText(/Couldn't load scan history/)).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: /export history/i })).toBeInTheDocument();
  });

  it("still says 'Run more scans' (no error) when history simply has too few points", async () => {
    renderPage();
    await runScan();

    expect(await screen.findByText("Run more scans to see a trend")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load scan history/)).not.toBeInTheDocument();
  });

  it("only offers the CSV export once scan history exists", async () => {
    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    // History mock defaults to [] -> no export button.
    await waitFor(() => expect(analyticsHistoryMock).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /export history/i })).not.toBeInTheDocument();
  });

  it("downloads a CSV built from the export endpoint", async () => {
    analyticsHistoryMock.mockResolvedValue([
      { id: 1, owner: "acme", score: 70, total_checks: 2, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ]);
    analyticsExportMock.mockResolvedValue({
      truncated: false,
      row_count: 1,
      entries: [
        {
          id: 1,
          owner: "acme",
          score: 70,
          total_checks: 2,
          failed_checks: 1,
          created_at: "2026-07-10T00:00:00Z",
          checks: [
            { id: "organization_members_mfa_required", title: "Org 2FA", severity: "high", status: "fail" },
          ],
        },
      ],
    });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });

    const exportButton = await screen.findByRole("button", { name: /export history/i });
    fireEvent.click(exportButton);

    await waitFor(() => expect(analyticsExportMock).toHaveBeenCalledWith("acme", undefined, undefined));
    await waitFor(() => expect(downloadTextFileMock).toHaveBeenCalled());
    const [filename, csv, mime] = downloadTextFileMock.mock.calls[0];
    expect(filename).toMatch(/^clevis-scan-history-acme-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(mime).toBe("text/csv");
    expect(csv).toContain("scanned_at,owner,score,total_checks,failed_checks,check_id,check_title,severity,status");
    expect(csv).toContain("organization_members_mfa_required");
  });

  it("passes the chosen date window to the export and tolerates entries without checks", async () => {
    analyticsHistoryMock.mockResolvedValue([
      { id: 1, owner: "acme", score: 70, total_checks: 2, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ]);
    analyticsExportMock.mockResolvedValue({
      truncated: false,
      row_count: 1,
      entries: [
        // no `checks` key -> the page must fall back to a single summary row
        { id: 1, owner: "acme", score: 70, total_checks: 2, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
      ],
    });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    await screen.findByRole("button", { name: /export history/i });

    fireEvent.change(screen.getByLabelText("Export from date"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("Export to date"), { target: { value: "2026-06-30" } });
    fireEvent.click(screen.getByRole("button", { name: /export history/i }));

    await waitFor(() =>
      expect(analyticsExportMock).toHaveBeenCalledWith("acme", "2026-01-01", "2026-06-30"),
    );
    const [, csv] = downloadTextFileMock.mock.calls[0];
    // One data row, all check columns empty.
    expect(csv.trim().split("\r\n")).toHaveLength(2);
    expect(csv).toContain("2026-07-10T00:00:00Z,acme,70,2,1,,,,");
  });

  it("shows an inline error when the export request fails", async () => {
    analyticsHistoryMock.mockResolvedValue([
      { id: 1, owner: "acme", score: 70, total_checks: 2, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ]);
    analyticsExportMock.mockRejectedValue(new Error("export blew up"));

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(await screen.findByRole("button", { name: /export history/i }));

    expect(await screen.findByText("export blew up")).toBeInTheDocument();
    expect(downloadTextFileMock).not.toHaveBeenCalled();
  });

  it("warns when the export was truncated at the row cap", async () => {
    analyticsHistoryMock.mockResolvedValue([
      { id: 1, owner: "acme", score: 70, total_checks: 2, failed_checks: 1, created_at: "2026-07-10T00:00:00Z" },
    ]);
    analyticsExportMock.mockResolvedValue({ truncated: true, row_count: 5000, entries: [] });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(await screen.findByRole("button", { name: /export history/i }));

    expect(await screen.findByText(/capped at 5000 scans/i)).toBeInTheDocument();
  });

  it("lists failed checks first, then errored ones, then passing, whatever order the API returns", async () => {
    const check = (id: string, status: string) => ({
      id, title: `Title ${id}`, severity: "medium", remediation: "n/a", status, value: null,
    });
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 50, total_checks: 6, failed_checks: 3, repo_count: 1,
      checks: [
        check("p1", "pass"), check("e1", "error"), check("p2", "pass"),
        check("f1", "fail"), check("e2", "error"), check("p3", "pass"),
      ],
    });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    await screen.findByText("Title f1");
    const order = screen.getAllByText(/^Title [fpe]\d$/).map((el) => el.textContent);
    expect(order).toEqual(["Title f1", "Title e1", "Title e2", "Title p1", "Title p2", "Title p3"]);
  });

  it("filters checks down to failed only via the Failed tab", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 50,
      total_checks: 2,
      failed_checks: 1,
      repo_count: 1,
      checks: [
        {
          id: "check-a",
          title: "Passing check",
          severity: "low",
          remediation: "n/a",
          status: "pass",
          value: null,
        },
        {
          id: "check-b",
          title: "Failing check",
          severity: "high",
          remediation: "n/a",
          status: "fail",
          value: null,
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    await waitFor(() => expect(screen.getByText("Passing check")).toBeInTheDocument());
    expect(screen.getByText("Failing check")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Failed" }));

    await waitFor(() => expect(screen.queryByText("Passing check")).not.toBeInTheDocument());
    expect(screen.getByText("Failing check")).toBeInTheDocument();
  });

  describe("bulk fix panel", () => {
    const MATRIX = {
      owner: "acme",
      repos: [
        {
          repo: "api",
          branch_protection: true,
          secret_scanning: false,
          dependabot_enabled: true,
          dependabot_critical_count: 0,
          dependabot_high_count: 0,
          code_scanning: true,
          force_push_allowed: false,
          score: 80,
          unknown_dimensions: [],
          alerts_source: "github",
        },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    };

    async function scanAcme() {
      analyticsOverviewMock.mockResolvedValue({ owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [] });
      securityMatrixMock.mockResolvedValue(MATRIX);
      renderPage();
      fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
      await waitFor(() => expect(orgsMineMock).toHaveBeenCalled());
      fireEvent.click(screen.getByRole("button", { name: /run scan/i }));
      await waitFor(() => expect(screen.getByText("Compliance Matrix")).toBeInTheDocument());
    }

    it("is offered to an org admin once the matrix has loaded, scoped to that org's repos", async () => {
      orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "admin" }]);
      try {
        await scanAcme();

        expect(await screen.findByText("Fix many repos at once")).toBeInTheDocument();
        expect(screen.getByRole("checkbox", { name: "api" })).toBeInTheDocument();
      } finally {
        orgsMineMock.mockResolvedValue([]);
      }
    });

    it("is hidden from a plain member, who would only get a 403", async () => {
      orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
      try {
        await scanAcme();

        expect(screen.queryByText("Fix many repos at once")).not.toBeInTheDocument();
      } finally {
        orgsMineMock.mockResolvedValue([]);
      }
    });
  });

  it("hides org-admin actions from a plain member of the scanned org", async () => {
    orgsMineMock.mockResolvedValue([{ org_login: "acme", role: "member" }]);
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 40,
      total_checks: 1,
      failed_checks: 1,
      repo_count: 1,
      checks: [
        {
          id: "repository_secret_scanning_enabled",
          title: "Secret scanning enabled",
          severity: "high",
          remediation: "Enable secret scanning.",
          status: "fail",
          value: null,
        },
      ],
    });
    try {
      renderPage();
      fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
      const scanButton = screen.getByRole("button", { name: /run scan/i });
      await waitFor(() => expect(scanButton).not.toBeDisabled());
      await waitFor(() => expect(orgsMineMock).toHaveBeenCalled());
      fireEvent.click(scanButton);

      await waitFor(() => expect(screen.getByText("Secret scanning enabled")).toBeInTheDocument());
      expect(screen.queryByRole("button", { name: /fix this/i })).not.toBeInTheDocument();
      expect(screen.queryByText("File as issue")).not.toBeInTheDocument();
    } finally {
      orgsMineMock.mockResolvedValue([]);
    }
  });

  it("applies a 'Fix this' remediation from a failing check and re-runs the scan", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 40,
      total_checks: 1,
      failed_checks: 1,
      repo_count: 1,
      checks: [
        {
          id: "repository_secret_scanning_enabled",
          title: "Secret scanning enabled",
          severity: "high",
          remediation: "Enable secret scanning.",
          status: "fail",
          value: null,
        },
      ],
    });
    remediateMock.mockResolvedValue({ check_id: "repository_secret_scanning_enabled", repo: "api", remediated: true });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    await waitFor(() => expect(screen.getByText("Secret scanning enabled")).toBeInTheDocument());
    expect(analyticsOverviewMock).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText("Repository to fix"), { target: { value: "api" } });
    fireEvent.click(screen.getByRole("button", { name: /fix this/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm — apply the fix/i }));

    await waitFor(() =>
      expect(remediateMock).toHaveBeenCalledWith("acme", "api", "repository_secret_scanning_enabled", ""),
    );
    // onRemediated -> runScan() fires a fresh scan.
    await waitFor(() => expect(analyticsOverviewMock).toHaveBeenCalledTimes(2));
  });

  it("narrows to a single severity via the By Severity tab's inline select", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme",
      score: 50,
      total_checks: 2,
      failed_checks: 2,
      repo_count: 1,
      checks: [
        { id: "check-a", title: "High severity check", severity: "high", remediation: "n/a", status: "fail", value: null },
        { id: "check-b", title: "Low severity check", severity: "low", remediation: "n/a", status: "fail", value: null },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    const scanButton = screen.getByRole("button", { name: /run scan/i });
    await waitFor(() => expect(scanButton).not.toBeDisabled());
    fireEvent.click(scanButton);

    await waitFor(() => expect(screen.getByText("High severity check")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "By Severity" }));
    const severitySelect = await screen.findByDisplayValue("All severities");
    fireEvent.change(severitySelect, { target: { value: "high" } });

    await waitFor(() => expect(screen.queryByText("Low severity check")).not.toBeInTheDocument());
    expect(screen.getByText("High severity check")).toBeInTheDocument();
  });

  it("loads the compliance matrix alongside a scan and renders repo rows", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        {
          repo: "api",
          branch_protection: true,
          secret_scanning: true,
          dependabot_enabled: true,
          dependabot_critical_count: 1,
          dependabot_high_count: 0,
          code_scanning: true,
          force_push_allowed: false,
          score: 80,
          unknown_dimensions: [],
        },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 1, secret_hits_count: 0, vuln_by_severity: { critical: 1, high: 0, medium: 0, low: 0 } },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(securityMatrixMock).toHaveBeenCalledWith("acme", ""));
    await waitFor(() => expect(screen.getByText("Compliance Matrix")).toBeInTheDocument());
    expect(screen.getAllByText("api").length).toBeGreaterThan(0);
    expect(secretScanningMock).toHaveBeenCalledWith("acme", "api", "");
  });

  it("gives the scan inputs accessible names", () => {
    renderPage();
    expect(screen.getByLabelText("Organization")).toBe(screen.getByPlaceholderText("e.g. octocat"));
    const token = screen.getByLabelText("GitHub Token");
    expect(token).toHaveAttribute("type", "password");
    // the optional-token guidance stays announced with the field
    expect(token).toHaveAccessibleDescription(/optional if the GitHub App is connected/);
  });

  it("lets keyboard users pick a matrix row through a real button", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    const row = (repo: string) => ({
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
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [row("api"), row("web")],
      summary: { fully_compliant_count: 2, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });

    renderPage();
    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    const api = await screen.findByRole("button", { name: "api" });
    const web = screen.getByRole("button", { name: "web" });
    expect(api).toHaveAttribute("aria-current", "true");
    expect(web).not.toHaveAttribute("aria-current");

    fireEvent.click(web);
    await waitFor(() => expect(web).toHaveAttribute("aria-current", "true"));
    expect(api).not.toHaveAttribute("aria-current");
  });

  it("shows a '?' for dimensions the token couldn't evaluate, not a false pass", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        {
          repo: "api",
          branch_protection: true,
          secret_scanning: true,
          dependabot_enabled: false,
          dependabot_critical_count: 0,
          dependabot_high_count: 0,
          code_scanning: true,
          force_push_allowed: false,
          score: 100,
          unknown_dimensions: ["dependabot"],
        },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(screen.getByText("Compliance Matrix")).toBeInTheDocument());
    expect(screen.getByTitle("unknown — token can't see this")).toHaveTextContent("?");
  });

  it("shows a loading skeleton, then a distinguishable error, when the compliance matrix fails", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    let rejectMatrix: (err: unknown) => void = () => {};
    securityMatrixMock.mockImplementation(() => new Promise((_, rej) => { rejectMatrix = rej; }));

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(screen.getByText("Compliance Matrix")).toBeInTheDocument());

    rejectMatrix(new Error("No GitHub App installation found for 'acme'"));

    await waitFor(() => {
      expect(screen.getByText(/Compliance matrix unavailable:/)).toBeInTheDocument();
    });
  });

  it("marks all dimensions unknown when the token can't see any of them, and lets the user click a row and switch repos", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        {
          repo: "api",
          branch_protection: false,
          secret_scanning: false,
          dependabot_enabled: false,
          dependabot_critical_count: 0,
          dependabot_high_count: 0,
          code_scanning: false,
          force_push_allowed: false,
          score: 0,
          unknown_dimensions: ["branch_protection", "dependabot", "code_scanning", "force_push"],
        },
        {
          repo: "worker",
          branch_protection: true,
          secret_scanning: true,
          dependabot_enabled: true,
          dependabot_critical_count: 0,
          dependabot_high_count: 0,
          code_scanning: true,
          force_push_allowed: false,
          score: 100,
          unknown_dimensions: [],
        },
      ],
      summary: { fully_compliant_count: 1, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(screen.getByText("Compliance Matrix")).toBeInTheDocument());
    expect(screen.getAllByTitle("unknown — token can't see this").length).toBeGreaterThanOrEqual(3);

    fireEvent.click(screen.getAllByText("worker")[0]);
    await waitFor(() => expect(secretScanningMock).toHaveBeenCalledWith("acme", "worker", ""));

    const repoSelect = screen.getByDisplayValue("worker");
    fireEvent.change(repoSelect, { target: { value: "api" } });
    await waitFor(() => expect(secretScanningMock).toHaveBeenCalledWith("acme", "api", ""));
  });

  it("surfaces an error instead of crashing when secret scanning fails", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        { repo: "api", branch_protection: true, secret_scanning: false, dependabot_enabled: true, dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false, score: 80, unknown_dimensions: [] },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockRejectedValue(new Error("GitHub API error: 403"));

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => {
      expect(screen.getByText("GitHub API error: 403")).toBeInTheDocument();
    });
  });

  it("renders a resolved secret alert without the open-state styling", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        { repo: "api", branch_protection: true, secret_scanning: false, dependabot_enabled: true, dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false, score: 80, unknown_dimensions: [] },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 1, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockResolvedValue({
      repository: "acme/api",
      alerts: [
        {
          number: 2,
          state: "resolved",
          secret_type: "github_personal_access_token",
          secret_type_display: "GitHub Personal Access Token",
          resolved_reason: "revoked",
          created_at: "2026-07-01T00:00:00Z",
          resolved_at: "2026-07-02T00:00:00Z",
          repo: "acme/api",
          url: "https://github.com/acme/api/security/secret-scanning/2",
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => {
      expect(screen.getByText("resolved")).toBeInTheDocument();
    });
  });

  it("never renders a raw secret value in the alerts list", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        { repo: "api", branch_protection: true, secret_scanning: false, dependabot_enabled: true, dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false, score: 80, unknown_dimensions: [] },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 1, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockResolvedValue({
      repository: "acme/api",
      alerts: [
        {
          number: 1,
          state: "open",
          secret_type: "github_personal_access_token",
          secret_type_display: "GitHub Personal Access Token",
          resolved_reason: null,
          created_at: "2026-07-01T00:00:00Z",
          resolved_at: null,
          repo: "acme/api",
          url: "https://github.com/acme/api/security/secret-scanning/1",
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(screen.getByText("GitHub Personal Access Token")).toBeInTheDocument());
    expect(screen.getByText("Secret values are never shown here — metadata only.")).toBeInTheDocument();
  });

  it("labels the matrix and secret-scanning panels as estimated when aggregate-sourced", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        { repo: "api", branch_protection: true, secret_scanning: false, dependabot_enabled: true, dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false, score: 80, unknown_dimensions: [], alerts_source: "aggregate" },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockResolvedValue({ repository: "acme/api", alerts: [], source: "aggregate" });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(secretScanningMock).toHaveBeenCalled());
    const captions = screen.getAllByText("(estimated)");
    expect(captions).toHaveLength(2);
  });

  it("does not label the panels as estimated when github-sourced", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        { repo: "api", branch_protection: true, secret_scanning: false, dependabot_enabled: true, dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false, score: 80, unknown_dimensions: [], alerts_source: "github" },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 0, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockResolvedValue({ repository: "acme/api", alerts: [], source: "github" });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(secretScanningMock).toHaveBeenCalled());
    expect(screen.queryByText("(estimated)")).not.toBeInTheDocument();
  });

  it("renders an aggregate-sourced alert with no url as a non-link row", async () => {
    analyticsOverviewMock.mockResolvedValue({
      owner: "acme", score: 100, total_checks: 0, failed_checks: 0, repo_count: 0, checks: [],
    });
    securityMatrixMock.mockResolvedValue({
      owner: "acme",
      repos: [
        { repo: "api", branch_protection: true, secret_scanning: false, dependabot_enabled: true, dependabot_critical_count: 0, dependabot_high_count: 0, code_scanning: true, force_push_allowed: false, score: 80, unknown_dimensions: [], alerts_source: "aggregate" },
      ],
      summary: { fully_compliant_count: 0, critical_risk_count: 0, secret_hits_count: 1, vuln_by_severity: { critical: 0, high: 0, medium: 0, low: 0 } },
    });
    secretScanningMock.mockResolvedValue({
      repository: "acme/api",
      source: "aggregate",
      alerts: [
        {
          number: 1,
          state: "open",
          secret_type: "github_personal_access_token",
          secret_type_display: "GitHub Personal Access Token",
          resolved_reason: null,
          created_at: "2026-07-01T00:00:00Z",
          resolved_at: null,
          repo: "acme/api",
          url: null,
        },
      ],
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("e.g. octocat"), { target: { value: "acme" } });
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));

    await waitFor(() => expect(screen.getByText("GitHub Personal Access Token")).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /GitHub Personal Access Token/i })).not.toBeInTheDocument();
  });
});
