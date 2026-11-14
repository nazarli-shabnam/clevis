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
})
