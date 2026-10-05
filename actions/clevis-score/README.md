# Clevis score gate

Fail a workflow when your organization's Clevis security score drops below a threshold, or when specific checks are failing.

```yaml
jobs:
  security-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: nazarli-shabnam/clevis/actions/clevis-score@main   # pin a tag or SHA in real use
        with:
          api-url: https://clevis.example.com
          org: acme
          token: ${{ secrets.CLEVIS_API_TOKEN }}
          threshold: 80
          fail-on-checks: repository_default_branch_protection_enabled,repository_secret_scanning_enabled
          refresh: "false"   # "true" runs a fresh scan first (uses the org's GitHub App/token)
```

## Getting a token

An org admin creates one with `POST /orgs/{org}/api-tokens` (the plaintext is returned once). Tokens are
read-only, bound to a single org, revocable (`DELETE /orgs/{org}/api-tokens/{id}`), and every create/revoke is
audit-logged. A token can read the latest score (`GET /api/v1/orgs/{org}/score`) and trigger a fresh scan
(`POST /api/v1/orgs/{org}/scan`), nothing that changes configuration.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Gate passed |
| 1 | Score below the threshold, or a named check is failing/errored/missing |
| 2 | Could not obtain a score (bad config, HTTP error, unreachable API, connection/TLS error or unreadable response) |

The script uses only the Python standard library, so it also works as a plain CLI:
`CLEVIS_API_URL=... CLEVIS_ORG=acme CLEVIS_TOKEN=... CLEVIS_THRESHOLD=80 python3 check.py`.

## Score badge

Opt in per org with `PUT /orgs/{org}/badge` (`{"enabled": true}`), then embed
`https://<your-clevis>/badges/<org>/score.svg`. The badge shows only the bare score; disabled, unknown and
never-scanned orgs all return the same 404.
