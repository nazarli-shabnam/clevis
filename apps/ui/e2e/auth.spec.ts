import { expect, test } from "@playwright/test"

import { E2E_API_BASE } from "../playwright.config"
import { E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD } from "./constants"
import { loginAsAdmin } from "./helpers"

test.describe("Login", () => {
  test("valid credentials redirect to the app", async ({ page }) => {
    await loginAsAdmin(page)

    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible()
  })

  test("invalid password shows an error and stays on /login", async ({ page }) => {
    await page.goto("/login")
    await page.getByPlaceholder("you@example.com").fill(E2E_ADMIN_EMAIL)
    await page.getByPlaceholder("Password").fill("definitely-the-wrong-password")
    await page.getByRole("button", { name: "Sign in", exact: true }).click()

    await expect(page.getByText(/invalid credentials/i)).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe("GitHub OAuth error redirect", () => {
  test("shows a clear message for github_oauth_failed and strips the query param", async ({ page }) => {
    await page.goto("/login?error=github_oauth_failed")

    await expect(page.getByText(/GitHub sign-in failed/i)).toBeVisible()
    // The page strips ?error= from the URL once read, so a refresh doesn't re-show it.
    await expect(page).toHaveURL((url) => !url.search.includes("error="))
  })
})

test.describe("Logout", () => {
  test("manual sign-out clears the session and returns to /login", async ({ page }) => {
    await loginAsAdmin(page)
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible()

    // Sidebar header button opens ProfileDropdown, whose "Sign out" calls logout().
    await page.getByRole("button", { name: /E2E Admin/i }).click()
    await page.getByRole("button", { name: "Sign out" }).click()

    await expect(page).toHaveURL(/\/login/)

    // Reloading a protected route must bounce to /login, proving the session was actually cleared.
    await page.goto("/security")
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe("Mid-session 401", () => {
  test("a 401 from the API redirects to /login without a loop", async ({ page }) => {
    await loginAsAdmin(page)
    await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible()

    // Simulate an expired session on /audit's on-mount fetch. Scoped to the API host so it doesn't
    // match the UI's own /audit navigation; trailing "**" covers query params.
    await page.route(`${E2E_API_BASE}/audit**`, (route) => route.fulfill({ status: 401, body: "{}" }))
    await page.goto("/audit")

    // AuthGuard must logout() before redirecting, or /login's "already authenticated" check
    // bounces back to the protected page and 401s again (infinite redirect loop).
    await expect(page).toHaveURL(/\/login/, { timeout: 5000 })
    await page.waitForTimeout(1000)
    await expect(page).toHaveURL(/\/login/)
  })
})
