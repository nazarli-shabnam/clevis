import { expect, test } from "@playwright/test"

import { E2E_API_BASE } from "../playwright.config"
import { loginAsAdmin } from "./helpers"

test.describe("Settings — org connection surface", () => {
  test("shows the GitHub App install path after login", async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto("/settings")

    // Real GitHub OAuth can't run in CI, so this asserts the install surface is reachable and wired.
    await expect(page.getByText("Connected GitHub accounts")).toBeVisible()
    await expect(
      page.getByText(/No accounts connected yet|Install the Clevis GitHub App/i),
    ).toBeVisible()

    // Install button shows when NEXT_PUBLIC_GITHUB_APP_SLUG is set, else an enable hint; either
    // proves the connection section rendered.
    const installButton = page.getByRole("button", { name: /Install GitHub App/i })
    const missingSlugHint = page.getByText(/GitHub App integration isn.t set up/i)
    await expect(installButton.or(missingSlugHint)).toBeVisible()
  })
})

test.describe("Security scan", () => {
  test("runs a scan happy path without a real GitHub org (API mocked)", async ({ page }) => {
    await loginAsAdmin(page)

    const overview = {
      owner: "acme",
      score: 100,
      total_checks: 1,
      failed_checks: 0,
      repo_count: 3,
      checks: [
        {
          id: "organization_members_mfa_required",
          title: "MFA required for org members",
          status: "pass",
          severity: "high",
          value: true,
          remediation: "Require two-factor authentication for all organization members.",
        },
      ],
    }
    // The scan runs as a job: the POST answers 202 with an id (the URL carries ?async=1), then the UI polls it.
    await page.route(`${E2E_API_BASE}/me/analytics/overview?async=1`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue()
        return
      }
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ job_id: 1 }) })
    })
    await page.route(`${E2E_API_BASE}/jobs/1`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: 1,
          job_type: "api.analytics_overview",
          status: "done",
          result: JSON.stringify(overview),
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      })
    })

    await page.goto("/security")
    await expect(page.getByRole("heading", { level: 1, name: "Health & Security" })).toBeVisible()

    await page.getByPlaceholder("e.g. octocat").fill("acme")
    await page.getByRole("button", { name: "Run scan" }).click()

    // The first poll comes 2 s after the job starts.
    await expect(page.getByText("Results — acme")).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText("MFA required for org members")).toBeVisible()
    await expect(page.getByLabel(/Security score: 100/i)).toBeVisible()
  })

  test("surfaces a clear error when the API has no installation token", async ({ page }) => {
    await loginAsAdmin(page)

    await page.route(`${E2E_API_BASE}/me/analytics/overview`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue()
        return
      }
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({
          detail:
            "No GitHub App installation found for 'missing-org' and no token was provided. " +
            "Install the GitHub App for this organization in Settings → Connected orgs.",
        }),
      })
    })

    await page.goto("/security")
    await page.getByPlaceholder("e.g. octocat").fill("missing-org")
    await page.getByRole("button", { name: "Run scan" }).click()

    await expect(page.getByTestId("scan-error")).toContainText(/No GitHub App installation found for 'missing-org'/i)
  })
})
