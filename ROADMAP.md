# Roadmap

Stage status only — no session logs, no history. For what's actually being worked on right now, check open PRs and issues; for what's done, check closed issues and `git log`. This file exists to answer one question: **what phase are we at?**

## Stage status

| Stage | Status |
|---|---|
| S0–S6 (core product: auth, RBAC, GitHub App, analytics, checks, multi-tenancy, event ingestion, aggregates) | Done |
| S7 — Billing + self-serve signup | Not started ([#194](https://github.com/nazarli-shabnam/clevis/issues/194)) |
| S8 — Platform hardening (OTel/Sentry/KMS/Terraform/HA) | Not started ([#195](https://github.com/nazarli-shabnam/clevis/issues/195)) |
| S9-gated: Repository Intelligence, Insights/DORA analytics | Not started ([#196](https://github.com/nazarli-shabnam/clevis/issues/196), [#197](https://github.com/nazarli-shabnam/clevis/issues/197)) |

Also shipped, outside the S0–S9 numbering: the write-access automation batch (bulk branch-protection/repo-settings apply, auto-triage, stale-PR nudges, workflow-policy linting, scheduled digests, compliance exports — [#288](https://github.com/nazarli-shabnam/clevis/issues/288)–[#294](https://github.com/nazarli-shabnam/clevis/issues/294)) and a general bug/doc-accuracy sweep ([#406](https://github.com/nazarli-shabnam/clevis/issues/406)+).

## What's next

No S7/S8/S9 work is scheduled — each is blocked on missing prerequisites (billing provider credentials, observability/infra accounts) rather than being actively planned against. Until one of those is unblocked, work is bug fixes, doc accuracy, and small hardening issues filed against `main`.

## Why this file, not `docs/plan.md`

`docs/plan.md` was a detailed working log (session notes, phase-by-phase design decisions) — useful while writing it, but `.gitignore` keeps the whole `docs/` directory untracked (except the force-added `docs/self-hosting.md`), so it was never actually in the repo, drifted out of sync with reality, and nothing caught that drift. This file is deliberately the opposite: small enough to keep accurate, and it says so explicitly when it might not be — check it against the linked issues rather than trusting the prose alone.
