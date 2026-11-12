import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getMock = vi.fn();
const setMock = vi.fn();
const repoMock = vi.fn();

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: {
      automationSchedules: (...a: unknown[]) => getMock(...a),
      setAutomationSchedules: (...a: unknown[]) => setMock(...a),
      setPrNudgeRepo: (...a: unknown[]) => repoMock(...a),
    },
  },
}));

import { AutomationSchedulesCard } from "@/components/automation-schedules-card";
import type { AutomationSchedules } from "@/lib/api/types";

const off = { enabled: null, effective: false, cadence: null, instance_cadence: "off" as const };
const schedules = (over: Partial<AutomationSchedules> = {}): AutomationSchedules => ({
  pr_nudges: off,
  dependabot_triage: off,
  pr_nudge_repos: [],
  triage_repos: ["acme/api"],
  ...over,
});

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AutomationSchedulesCard orgLogin="acme" />
    </QueryClientProvider>,
  );
}

describe("AutomationSchedulesCard", () => {
  beforeEach(() => {
    [getMock, setMock, repoMock].forEach((m) => m.mockReset());
    getMock.mockResolvedValue(schedules());
  });
  afterEach(cleanup);

  it("lets an admin switch scheduled nudges on for the org (#833)", async () => {
    setMock.mockResolvedValue(schedules({ pr_nudges: { enabled: true, effective: true, cadence: "weekly", instance_cadence: "off" } }));
    renderCard();

    fireEvent.change(await screen.findByLabelText("Nudge stale pull requests automatically"), { target: { value: "on" } });

    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", { pr_nudges: true }));
    expect(await screen.findByText("Runs weekly.")).toBeInTheDocument();
  });

  it("adds a repository to scheduled nudges, defaulting the owner to the org, and removes one", async () => {
    getMock.mockResolvedValue(schedules({ pr_nudge_repos: ["acme/web"] }));
    repoMock.mockResolvedValue({ enabled: true });
    renderCard();

    fireEvent.change(await screen.findByLabelText("Repository to nudge"), { target: { value: "api" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(repoMock).toHaveBeenCalledWith("acme", "acme", "api", true));

    fireEvent.click(screen.getByRole("button", { name: "Stop nudging acme/web" }));
    await waitFor(() => expect(repoMock).toHaveBeenLastCalledWith("acme", "acme", "web", false));
  });

  it("rejects a repository with extra path segments instead of enabling a different one", async () => {
    renderCard();

    fireEvent.change(await screen.findByLabelText("Repository to nudge"), { target: { value: "acme/api/extra" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/name or owner\/name/);
    expect(repoMock).not.toHaveBeenCalled();
  });

  it("says a scheduled run does nothing until a repository is added, and shows the triage repo count", async () => {
    renderCard();

    expect(await screen.findByText(/does nothing until a repository is added/)).toBeInTheDocument();
    expect(screen.getByText(/1 repository enabled in the/)).toBeInTheDocument();
  });

  it("shows an error with a retry when the schedules cannot be loaded", async () => {
    getMock.mockRejectedValueOnce(new Error("boom"));
    renderCard();

    expect(await screen.findByText(/Couldn't load the automation schedules: boom/)).toBeInTheDocument();
    getMock.mockResolvedValue(schedules());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Triage Dependabot pull requests automatically")).toBeInTheDocument();
  });

  it("lets an admin switch scheduled Dependabot triage off for the org", async () => {
    setMock.mockResolvedValue(schedules());
    renderCard();

    fireEvent.change(await screen.findByLabelText("Triage Dependabot pull requests automatically"), { target: { value: "off" } });
    await waitFor(() => expect(setMock).toHaveBeenCalledWith("acme", { dependabot_triage: false }));
  });

  it("surfaces an error from saving a schedule or a repository", async () => {
    setMock.mockRejectedValue(new Error("save failed"));
    renderCard();

    fireEvent.change(await screen.findByLabelText("Triage Dependabot pull requests automatically"), { target: { value: "on" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("save failed");
  });
});
