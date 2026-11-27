import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const downloadMock = vi.fn()
vi.mock("@/lib/download", () => ({ downloadTextFile: (...a: unknown[]) => downloadMock(...a) }))

import { ComplianceMatrix } from "@/components/compliance-matrix"
import type { RepoSecurityRow } from "@/lib/api/types"

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
  }
}

const REPOS = [
  row("good"),
  row("no-branch", { branch_protection: false, score: 80 }),
  row("blind", { unknown_dimensions: ["branch_protection"], score: 90 }),
  row("noisy", { dependabot_critical_count: 2, score: 70 }),
]

function renderMatrix(props: Partial<React.ComponentProps<typeof ComplianceMatrix>> = {}) {
  return render(<ComplianceMatrix owner="acme" repos={REPOS} selectedRepo="" onSelectRepo={vi.fn()} {...props} />)
}

const repoOrder = () =>
  screen.getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent)

beforeEach(() => downloadMock.mockReset())
afterEach(cleanup)

describe("ComplianceMatrix", () => {
  it("sorts a check column worst first: failing, then unknown, then passing", () => {
    renderMatrix()
    fireEvent.click(screen.getByRole("button", { name: "Branch" }))
    expect(repoOrder().slice(0, 2)).toEqual(["no-branch", "blind"])
    expect(repoOrder().slice(2).sort()).toEqual(["good", "noisy"])
  })

  it("sorts by score", () => {
    renderMatrix()
    fireEvent.click(screen.getByRole("button", { name: "Score" }))
    expect(repoOrder()).toEqual(["noisy", "no-branch", "blind", "good"])
  })

  it("filters to failing or unknown repos, for one check or any", () => {
    renderMatrix()
    fireEvent.click(screen.getByLabelText("Failing or unknown only"))
    expect(repoOrder().sort()).toEqual(["blind", "no-branch", "noisy"])
    expect(screen.getByText("3 of 4 repositories")).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText("Check to filter by"), { target: { value: "branch_protection" } })
    expect(repoOrder().sort()).toEqual(["blind", "no-branch"]) // unknown counts as needing attention
  })

  it("exports the filtered view, with each check as pass/fail/unknown", () => {
    renderMatrix()
    fireEvent.click(screen.getByLabelText("Failing or unknown only"))
    fireEvent.change(screen.getByLabelText("Check to filter by"), { target: { value: "branch_protection" } })
    fireEvent.click(screen.getByRole("button", { name: /export csv/i }))

    const [filename, csv] = downloadMock.mock.calls[0]
    expect(filename).toMatch(/^clevis-security-matrix-acme-filtered-\d{4}-\d{2}-\d{2}\.csv$/)
    const lines = String(csv).trim().split(/\r?\n/)
    expect(lines[0]).toBe("Repo,Branch,Secrets,Dependabot,Code scan,Force-push,Score")
    expect(lines.slice(1).sort()).toEqual([
      "blind,unknown,pass,pass,pass,pass,90",
      "no-branch,fail,pass,pass,pass,pass,80",
    ])
  })

  it("hands the filtered repos to the bulk fixer only when asked and only for admins", () => {
    const onUse = vi.fn()
    const { rerender } = renderMatrix({ onUseInBulkFix: onUse })
    expect(screen.queryByRole("button", { name: /in the bulk fixer/ })).not.toBeInTheDocument() // no filter yet

    fireEvent.click(screen.getByLabelText("Failing or unknown only"))
    fireEvent.click(screen.getByRole("button", { name: "Select these 3 in the bulk fixer" }))
    expect(onUse.mock.calls[0][0].sort()).toEqual(["blind", "no-branch", "noisy"])

    rerender(<ComplianceMatrix owner="acme" repos={REPOS} selectedRepo="" onSelectRepo={vi.fn()} />)
    expect(screen.queryByRole("button", { name: /in the bulk fixer/ })).not.toBeInTheDocument()
  })

  it("selects a repo from its name", () => {
    const onSelect = vi.fn()
    renderMatrix({ onSelectRepo: onSelect })
    fireEvent.click(screen.getByRole("button", { name: "noisy" }))
    expect(onSelect).toHaveBeenCalledWith("noisy")
  })

  describe("controls column (#1277)", () => {
    const control = (framework: string, id: string, name: string) => ({ framework, id, name })
    const CONTROLS = {
      frameworks: [
        { id: "soc2", name: "SOC 2", version: "2017" },
        { id: "cis", name: "CIS Controls", version: "v8" },
      ],
      checks: {
        repository_default_branch_protection_enabled: [control("soc2", "CC8.1", "Change management"), control("cis", "16.1", "Secure development")],
        repository_dependabot_alerts_clear: [control("soc2", "CC7.1", "Vulnerability detection"), control("soc2", "CC8.1", "Change management")],
      },
    }
    const cells = () => screen.getAllByRole("row").slice(1).map((r) => {
      const c = within(r).getAllByRole("cell")
      return [c[0].textContent, c[c.length - 2].textContent]
    })

    it("shows no column or selector without a mapping", () => {
      renderMatrix()
      expect(screen.queryByText("Controls at risk")).not.toBeInTheDocument()
      expect(screen.queryByLabelText("Framework for the controls column")).not.toBeInTheDocument()
    })

    it("lists each repo's at-risk controls once, counting unknown checks, and a dash when none", () => {
      renderMatrix({ controls: CONTROLS, repos: [...REPOS, row("both", { branch_protection: false, dependabot_high_count: 1 })] })

      expect(cells()).toEqual([["good", "—"], ["no-branch", "CC8.1"], ["blind", "CC8.1"], ["noisy", "CC7.1, CC8.1"], ["both", "CC8.1, CC7.1"]])
      expect(screen.getByText("CC7.1, CC8.1")).toHaveAttribute("title", "CC7.1 Vulnerability detection\nCC8.1 Change management")
    })

    it("switches the framework and exports the chosen ids", () => {
      renderMatrix({ controls: CONTROLS })

      fireEvent.change(screen.getByLabelText("Framework for the controls column"), { target: { value: "cis" } })
      expect(cells()).toEqual([["good", "—"], ["no-branch", "16.1"], ["blind", "16.1"], ["noisy", "—"]])

      fireEvent.click(screen.getByRole("button", { name: /export csv/i }))
      const csv = String(downloadMock.mock.calls[0][1])
      expect(csv).toContain("Controls at risk")
      expect(csv.split(/\r?\n/).find((l) => l.startsWith("no-branch"))).toContain("16.1")
    })

    it("sorts the repos with the most controls at risk first", () => {
      renderMatrix({ controls: CONTROLS, repos: [...REPOS, row("both", { branch_protection: false, dependabot_high_count: 1 })] })

      fireEvent.click(screen.getByRole("button", { name: /Controls at risk/ }))
      expect(repoOrder().slice(0, 2).sort()).toEqual(["both", "noisy"])
    })
  })
})
