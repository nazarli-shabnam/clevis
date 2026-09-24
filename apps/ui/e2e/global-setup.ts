import { E2E_API_BASE } from "../playwright.config"
import { E2E_ADMIN_EMAIL, E2E_ADMIN_NAME, E2E_ADMIN_PASSWORD } from "./constants"

// Seeds the workspace admin via the API instead of the /setup wizard. /auth/setup 409s once any
// user exists, which just means a prior run already seeded this stack.
export default async function globalSetup(): Promise<void> {
  const res = await fetch(`${E2E_API_BASE}/auth/setup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: E2E_ADMIN_EMAIL,
      name: E2E_ADMIN_NAME,
      password: E2E_ADMIN_PASSWORD,
    }),
  })

  if (res.ok || res.status === 409) return

  const body = await res.text().catch(() => "")
  throw new Error(`E2E global setup failed: POST /auth/setup returned ${res.status} ${body}`)
}
