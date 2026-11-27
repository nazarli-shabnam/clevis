import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let role: "admin" | "member" = "admin";
const replaceMock = vi.fn();
let search = "";

vi.mock("next/navigation", () => ({
  useParams: () => ({ login: "acme" }),
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => new URLSearchParams(search),
}));

// The cards have their own tests; here only which of them the page mounts matters.
const { card } = vi.hoisted(() => ({
  card: (name: string) => () => <div data-testid={name} />,
}));
vi.mock("@/components/hygiene-scoring-card", () => ({ HygieneScoringCard: card("hygiene") }));
vi.mock("@/components/scoring-exclusions-card", () => ({ ScoringExclusionsCard: card("scoring-exclusions") }));
vi.mock("@/components/target-score-card", () => ({ TargetScoreCard: card("target-score") }));
vi.mock("@/components/scheduled-scans-card", () => ({ ScheduledScansCard: card("scheduled-scans") }));
vi.mock("@/components/digest-card", () => ({ DigestCard: card("digest") }));
vi.mock("@/components/automation-schedules-card", () => ({ AutomationSchedulesCard: card("automation") }));
vi.mock("@/components/waivers-card", () => ({ WaiversCard: card("waivers") }));
vi.mock("@/components/clevis-members-card", () => ({ ClevisMembersCard: card("clevis-access") }));
vi.mock("@/components/ingestion-health-card", () => ({ IngestionHealthCard: card("ingestion") }));
vi.mock("@/components/evidence-export-card", () => ({ EvidenceExportCard: card("evidence") }));
vi.mock("@/components/notification-destinations-card", () => ({ NotificationDestinationsCard: card("destinations") }));
vi.mock("@/components/badge-settings-card", () => ({ BadgeSettingsCard: card("badge") }));
vi.mock("@/components/api-tokens-card", () => ({ ApiTokensCard: card("api-tokens") }));
vi.mock("@/components/smtp-verify-notice", () => ({ SmtpVerifyNotice: card("smtp") }));

vi.mock("@/lib/api/client", () => ({
  api: {
    orgs: { mine: () => Promise.resolve([{ org_login: "acme", role }]) },
    invitations: { list: vi.fn().mockResolvedValue([]) },
    collab: {
      members: vi.fn().mockResolvedValue({ org: "acme", members: [], two_factor_overlay_available: false }),
      outsideCollaborators: vi.fn(),
      invitations: vi.fn().mockResolvedValue({ org: "acme", invitations: [] }),
      permissionAudit: vi.fn(),
      inactiveMembers: vi.fn(),
    },
    tokens: { resolve: vi.fn().mockRejectedValue(new Error("no saved token")) },
  },
}));
vi.mock("@/lib/use-digest-settings", () => ({ useDigestSettings: () => ({ data: undefined }) }));

import OrgSettingsPage from "@/app/settings/org/[login]/settings/page";
import OrgMembersPage from "@/app/settings/org/[login]/members/page";

function renderWith(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("org Settings page (#1130)", () => {
  beforeEach(() => {
    role = "admin";
    search = "";
    replaceMock.mockReset();
  });
  afterEach(cleanup);

  it("groups every org setting under a section with an anchor in the section nav", async () => {
    renderWith(<OrgSettingsPage />);
    await screen.findByTestId("hygiene");

    const sections: Record<string, string[]> = {
      access: ["clevis-access", "api-tokens"],
      scanning: ["hygiene", "scheduled-scans", "badge"],
      notifications: ["digest", "destinations"],
      automation: ["automation"],
      waivers: ["waivers"],
      health: ["ingestion", "evidence"],
    };
    for (const [id, cards] of Object.entries(sections)) {
      const section = document.getElementById(id)!;
      for (const c of cards) expect(section.querySelector(`[data-testid="${c}"]`)).not.toBeNull();
      expect(screen.getByRole("link", { name: new RegExp(`^${section.querySelector("h2")!.textContent}$`) })).toHaveAttribute("href", `#${id}`);
    }
  });

  it("explains to a plain member that settings are admin-only, mounting no card", async () => {
    role = "member";
    renderWith(<OrgSettingsPage />);

    expect(await screen.findByText(/limited to organization admins/)).toBeInTheDocument();
    expect(screen.queryByTestId("hygiene")).not.toBeInTheDocument();
  });
});

describe("Members page after the split (#1130)", () => {
  beforeEach(() => {
    role = "admin";
    search = "";
    replaceMock.mockReset();
  });
  afterEach(cleanup);

  it("no longer carries the settings cards, and links to Organization settings", async () => {
    renderWith(<OrgMembersPage />);

    expect(await screen.findByRole("link", { name: "Organization settings" })).toHaveAttribute("href", "/settings/org/acme/settings");
    expect(screen.queryByTestId("hygiene")).not.toBeInTheDocument();
    expect(screen.queryByTestId("waivers")).not.toBeInTheDocument();
    expect(screen.queryByTestId("api-tokens")).not.toBeInTheDocument();
  });

  it.each(["waiver=7", "token=3"])("forwards an old ?%s link to the settings page, query intact", async (query) => {
    search = query;
    renderWith(<OrgMembersPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith(`/settings/org/acme/settings?${query}`));
  });
});
