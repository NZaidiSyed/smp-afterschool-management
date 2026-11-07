## Release and scope

- Release ID in `docs/releases/register.json`:
- Exact candidate commit:
- Problem and resulting behavior:
- Data/schema/security impact:
- Rollback plan:

## Required evidence

- Read `AGENTS.md`, `APP_MASTER.md`, and `docs/RELEASE_POLICY.md` before changes.
- Update the active release and generated master history.
- Record tests run and passed/failed/skipped outcomes for this exact candidate.
- For production: record hosted UAT evidence, actual user approval reference,
  verified production target, backup/restore, security review and provider gates.
- Run `python scripts/release_guard.py validate --require-current`.
- For main-bound promotion, `python scripts/release_guard.py check-production`
  must pass. Never invent approval or mark a Git push as a verified deployment.

Do not include API keys, credential-bearing URLs or student/payment records.
