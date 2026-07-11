## Summary

<!-- What does this PR change, and why? -->

Closes #<!-- issue number, if any -->

## Checks

Mirrors [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — see [CONTRIBUTING.md](../CONTRIBUTING.md) for full commands.

- [ ] `cd apps/ui && bun run typecheck && bun run build` (if UI changed)
- [ ] `python -m compileall apps/api/src apps/worker/src` (if API/worker changed)
- [ ] Relevant `pytest` tests pass (if API/worker changed)
- [ ] Docker images still build, if `Dockerfile`/deps changed
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
