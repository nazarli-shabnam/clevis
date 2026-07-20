import type { Page } from "@playwright/test"

import { E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD } from "./constants"

/** Shared login helper for e2e specs — seeds user via global-setup.ts. */
export async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto("/login")
  await page.getByPlaceholder("you@example.com").fill(E2E_ADMIN_EMAIL)
  await page.getByPlaceholder("Password").fill(E2E_ADMIN_PASSWORD)
  await page.getByRole("button", { name: "Sign in", exact: true }).click()
  await page.waitForURL((url) => !url.pathname.startsWith("/login"))
}
