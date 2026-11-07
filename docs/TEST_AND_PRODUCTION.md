# Separate test and production environments

## Current status

Security changes are on `security/branch-isolation-staging`, not production `main`.
A reproducible **local** test environment has been exercised with synthetic data.
No hosted test service or Supabase project has been created, and production
records/configuration have not been accessed or modified by this work.

## Boundaries

| Resource | Test | Production |
| --- | --- | --- |
| Service | A new Render test service | Existing service |
| Database | Separate Supabase test project | Existing production project |
| Accounts | Synthetic test users; separate auth configuration | Approved real users |
| Secrets | Test-only keys and a distinct signing secret | Production-only credentials |
| Files and backups | Test-only storage | Restricted encrypted production storage |
| Email/payments | Disabled or provider sandbox | Explicit live configuration |

Do not point a test service at the production database, copy production secrets,
or import identifiable student/payment data into test. Test has the same security
requirements as production; local synthetic fixtures are explicitly different.

## Local test workflow

Use Python 3.12 and Node 24. Playwright and Chromium are available in the cloud
image. From the existing checkout (do not create a worktree unless requested):

```sh
python scripts/check_test_environment.py --browser --port 8798
```

The script refuses production DB/auth/mail variables, creates a unique retained
`/workspace/smp-security-test-uat-*` directory, installs dependencies in its own
venv, seeds synthetic attendance/staff data, runs Python and JS checks, rehearses the synthetic branch-code migration with a checked backup, then
starts/stops only its own browser-test server. It refuses an already occupied
port. It never reseeds or deletes an existing user's database.

The full 159-test run was also exercised here against a disposable local
PostgreSQL 17 cluster, including a real dump/restore rehearsal. The local
PostgreSQL binaries came from Debian's signed package repository and were
extracted into `/workspace/.smp-postgres-packages`; no system packages were
replaced. A first attempt used SQL_ASCII; the final cluster was explicitly
initialized as UTF8 and the full suite passed.

To restart the retained test cluster in this cloud snapshot:

```sh
/workspace/.smp-security-test-uat/venv/bin/python scripts/start_test_postgres.py
export PATH=/workspace/.smp-postgres-packages/root/usr/lib/postgresql/17/bin:$PATH
export SMP_TEST_POSTGRES_DSN='dbname=postgres user=agent host=/workspace/.smp-postgres-packages/socket port=55439'
python scripts/check_test_environment.py --browser --port 8798
```

The socket directory is private and the cluster has no TCP listener. Its local
trust authentication is only for disposable synthetic tests; it is not a hosted
production authentication model. A fresh machine without the retained packages
needs verified PostgreSQL tools installed first. CI installs its own tools.

The PostgreSQL tests skip locally when `SMP_TEST_POSTGRES_DSN` is absent. The
GitHub workflow configures a disposable PostgreSQL service to exercise those
suites. A workflow definition is not a successful CI run; check actual results.

## Hosted test setup

1. Provision a separate Supabase TEST project with its own database, users,
   storage, and authentication redirect URLs. Disable outgoing live notifications.
2. Prepare its schema and a single synthetic organization/branch through a
   reviewed bootstrap/migration process. Do not use the production database URL.
   This security release deliberately refuses an empty or multi-branch production
   database rather than silently creating or choosing a branch.
3. Use `render.test.yaml` for a NEW Render service. It requires secure test-only
   values for `DATABASE_URL`, `SMP_ORGANIZATION_ID`, `SUPABASE_URL`,
   `SUPABASE_ANON_KEY`, and `SUPABASE_PROJECT_REF`; Render generates a distinct
   `JWT_SECRET`. PostgreSQL URL must use `sslmode=verify-full` with the provider's
   trusted CA configured for libpq. Do not disable TLS verification.
4. Set the test branch code to `CA-YYC-KMN-0001`. Confirm its branch ID and linked
   records before running the migration. An identical business code in a separate
   synthetic TEST project does not reserve a second production code.
5. Explicitly provision a real Supabase test administrator and a corresponding
   active `app_users` membership. First-login admin promotion and email bypasses
   have been removed. Test secure login, expiry/revocation, access denials,
   representative workflows, and the migration with real PostgreSQL.
6. Keep auto-deploy off while doing migration rehearsals. Test the exact commit
   intended for production. Configure GitHub branch protection to require the
   security/regression workflow before merging to `main`.

## Branch-code reservation

Format: `COUNTRY-IATA-KMN-0001`. Approved pairs currently include Canada/Calgary
`CA-YYC` and Canada/Winnipeg `CA-YWG`. Additional countries/airports require a
reviewed registry entry; the software does not guess airport codes from names.

Use **one** restricted operator registry for production branch reservations, not
one independent registry per branch. `scripts/allocate_branch_code.py` serializes
allocations and permanently reserves codes by immutable deployment UUID. An
existing reservation is idempotent; a changed country/airport is rejected.
The registry contains only branch metadata, must have private permissions and a
backup, and must never be exposed as a public application asset.

Before allocating future Calgary codes, reserve `CA-YYC-KMN-0001` for the verified
existing production branch's deployment UUID in that authoritative registry. Do
not use the synthetic test registry as the production authority. Preserve closed
branch reservations; never delete them to reuse a number.

## Safe existing-branch rename

`CSC0001` is to become **CA-YYC-KMN-0001**. The branch ID stays unchanged; student,
attendance, financial, staff and other records are not recreated or moved.

Use `scripts/migrate_branch_code.py` with the verified `--expected-branch-id`.
It defaults to a read-only preflight and inventories all public application tables.
It rejects an unexpected old code, wrong branch ID, or multiple branches/orgs.
`--apply` requires a private `--backup-dir`; keep production backups on approved
encrypted storage. It fingerprints every application record excluding only the
branch-code field and checks equality in the rename transaction. Failed checks
roll back. It is idempotent when the code already matches.

For PostgreSQL, supply `SMP_MIGRATION_DATABASE_URL` through secure environment
settings; do not put a credential-bearing URL on the CLI. `pg_dump` and
`pg_restore` are mandatory for the backup. Archive readability is checked, but
**a PostgreSQL restore rehearsal is still required** before production use.
For SQLite, the backup is opened and its integrity and record fingerprints are
checked automatically. Stop application writes/background jobs during the final
production migration and retain the backup and inventory securely. Do not reset
or reseed production data.

## Promotion gate and outstanding work

Do not merge/deploy this staged release until hosted test evidence and secure
configuration are available. Production startup now requires pinned organization
and branch code, real Supabase authentication, verified database TLS, and a strong
signing secret. Missing configuration intentionally prevents startup. Existing
plaintext staff PIN/password records need a reviewed reset/migration; this release
rejects them in production. Existing staff tokens must be renewed because new
signed tokens bind to an organization/branch and check active membership.

This is a security foundation, not a completed professional security certification.
Before opening another branch:

- Verify all production record ownership and audit missing/foreign branch links;
  the code rename alone does not backfill or certify ownership.
- Complete live PostgreSQL migration/restore, real Supabase login and MFA, and
  deployed access-control testing. Set database roles and private storage grants
  explicitly; this change does not reconfigure hosted RLS policies or privileges.
- Finish the legacy staff UI/server endpoint authorization audit and session/XSS
  hardening. JavaScript-accessible session cookies remain; they are not HttpOnly.
  The added CSP limits framing/objects but is not a comprehensive script policy.
- Configure trusted edge/distributed login limits and alerts. The built-in limiter
  is per process and sees the actual socket peer; it never trusts arbitrary
  forwarded-IP headers. Supabase login limits are configured separately.
- Deploy each new branch with its own database, app/service identity, auth
  configuration, secrets, files, and backups. Shared-database branch operation is
  explicitly rejected; it needs a separate full tenancy/RLS migration and tests.

When these gates pass: back up, apply the reviewed branch rename, pin
`SMP_BRANCH_CODE=CA-YYC-KMN-0001`, deploy the tested commit, verify live login,
record counts/balances and denials, then monitor. Keep the previous code revision
and a tested recovery plan. Do not roll back by restoring a stale backup over
newer production writes.
