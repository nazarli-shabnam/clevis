import { defineConfig, devices } from "@playwright/test"

// Runs against a live stack started externally (docker compose in CI, or run
// manually for local dev) — this config does NOT start a dev server itself.
export const E2E_BASE_URL = process.env.E2E_BASE_URL || "http://localhost:3000"
export const E2E_API_BASE = process.env.E2E_API_BASE || "http://localhost:8080"

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: E2E_BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
})
