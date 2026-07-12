// Fixed test credentials, deterministic across runs. The E2E stack always starts from an
// empty database (fresh `docker compose up`), so there's no risk of clashing with real data —
// global-setup.ts seeds this exact user once before any test runs.
export const E2E_ADMIN_EMAIL = "e2e-admin@example.com"
export const E2E_ADMIN_PASSWORD = "e2e-test-password-123"
export const E2E_ADMIN_NAME = "E2E Admin"
