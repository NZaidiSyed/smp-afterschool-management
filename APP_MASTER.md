# SMP application master record

This is the entry point for all development, testing, UAT and production work.
Read this document and [Release policy](docs/RELEASE_POLICY.md) before changes.
`AGENTS.md` makes that requirement explicit for future coding agents.

Facts below distinguish inspected source and local evidence from unverified
hosted settings. Never store passwords, API keys, connection strings with
credentials, identifiable student records, or private backup contents here.

## Product and ownership

SMP manages after-school student rosters/enrolment, presence schedules, attendance
and barcodes, fees/payments, reconciliation, reporting, staff schedules, leave,
swaps, notifications, and centre configuration. Kumon is the current branch use
case. The repository is `NZaidiSyed/smp-afterschool-management` on GitHub.

Operations navigation is Dashboard -> Student Roster -> Student Barcode ->
Student Presence -> Student Attendance -> Reports. Finance and Admin have their
own workspace tabs. Existing role permissions still determine visible features.

## Current verified and unverified state

- Production source branch: `main`. Last remotely verified tip before this
  documentation work: `6f2ddc11c8b57085bc6d81700d5373ecf8aa199b`.
- Staged security work started at `6f0c18473ffa13b110b39db2a40dd25d9b6ba9f2`
  on `security/branch-isolation-staging`. It has not been promoted to production.
- The navigation change was pushed to main. Its live Render deployment was not
  verified; do not retroactively describe it as a verified live release.
- Local isolated SQLite and PostgreSQL 17 tests and browser workflows have run.
  The staged security baseline passed 159 Python tests without skips, 15 JS
  scenarios, and the scheduling browser workflow. Later release evidence belongs
  in the register, with the exact candidate and actual results.
- A hosted TEST service/project has not been created. Live production data,
  deployed branch code, database privileges/RLS, backups, and real login/MFA have
  not been verified by this work. The security release is not rollout-ready.
- `render.yaml` describes a Render service named
  `smp-afterschool-management`. `app.afterschools.org` is the documented app
  hostname; its actual hosting/service identity still needs verification.
- Render auto-deploy, GitHub branch protection, and required UAT reviewers are
  not verified/configured here. Configure them before relying on CI as a gate.

The release register and generated history below track subsequent changes; the
source revisions above are historical baselines, not a moving production claim.

## Architecture and code map

| Area | Source / guide |
| --- | --- |
| Python HTTP/API server, SQL access and startup | `app.py` |
| Main browser app, layout and workspace navigation | `index.html`, `app.js`, `styles.css`, `student-navigation.js` |
| Attendance domain and camera/scanner UI | `attendance.py`, `attendance-ui.js`, `camera-scanner.js`, `docs/STUDENT_ATTENDANCE_UAT.md` |
| Staff scheduling domain/UI | `staff_scheduling.py`, `staff-scheduler.*`, `docs/STAFF_SCHEDULING_UAT.md` |
| Legacy staff administration UI | `staffbase.html`; authorization audit remains open |
| Static asset cache | `sw.js`; do not cache API data or session-bearing URLs |
| Branch rules, isolation and code allocation | `branch_security.py`, `scripts/allocate_branch_code.py` |
| Existing-branch rename and data fingerprints | `scripts/migrate_branch_code.py` |
| Initial Supabase schema | `supabase/schema.sql`; not proof of applied live schema/policies |
| Hosting definitions | `render.yaml`, `render.test.yaml`, `Dockerfile`, `Procfile` |
| Synthetic setup and checks | `run_attendance_uat.py`, `run_staff_scheduling_uat.py`, `scripts/check_test_environment.py` |
| Release process and history | `docs/RELEASE_POLICY.md`, `docs/releases/register.json`, `scripts/release_guard.py` |

Python 3.12 and Node 24 are the exercised development tools. Dependencies are in
`requirements.txt`. PostgreSQL access uses psycopg; barcode rendering uses
python-barcode; password hashing uses bcrypt. Main runtime is Python, not the
separate historical `staffbase-server.js` Node server. There is no frontend
compilation step in the Render build definition. The Python built-in HTTP server
is used; do not describe it as a hardened production web-server stack.

## Data and branch rules

Local synthetic storage is SQLite. Documented production design is Supabase
PostgreSQL, with browser authentication through Supabase and app-level access
records. Organization/branch tables identify ownership; PostgreSQL IDs are UUIDs,
while local SQLite uses integer IDs. Do not assume every legacy table already
has mandatory branch foreign keys or every live query/policy is branch-safe.

The current staged deployment boundary is **one organization and one branch per
database**. It rejects multiple branches rather than selecting the first branch.
Future isolated branches need distinct databases/service identities/secrets and
explicit user grants. Shared-database tenancy requires a separate approved schema,
RLS/authorization migration and cross-branch tests.

Business code format: `COUNTRY-IATA-KMN-NNNN`. Country is a two-letter country
code; city is its designated three-letter IATA airport code; sequence starts at
0001 for each approved country/airport pair. Approved pairs currently include
CA/YYC (Calgary) and CA/YWG (Winnipeg). Add reviewed registry entries for other
locations; never guess or silently change location codes.

Existing branch target: **CSC0001 -> CA-YYC-KMN-0001**, retaining its existing
internal ID. Confirm the real source code/branch ID before migration; older
source also contains CCS001 examples. No production rename has been performed.
Codes are reserved atomically in one operator registry, never reused, and are
identifiers rather than access credentials. A move or closure does not silently
re-key historical records. Attendance defaults to America/Edmonton in inspected
source; future branches must verify their own IANA timezone and currency/tax rules.

## Environments and credentials

| Resource | Reuse rule |
| --- | --- |
| Render account-management API credential | Reuse the existing authorized secure binding for test and production management; keep it outside app runtimes |
| Supabase account-management access token | Reuse the existing authorized secure binding; explicitly select the intended project before every operation |
| Schema, dependency versions, feature behavior, branch-code rules | Keep aligned between test and production |
| DB connection/password, Supabase project URL/anon/service-role keys | Separate project-specific settings; never point test at production |
| JWT signing secret, real auth users/sessions, storage, webhook/mail/payment destinations | Separate per environment; test uses synthetic users and provider sandboxes |
| Production student/financial data and backups | Remain private; use synthetic or independently anonymized fixtures for test |

Existing production keys should not be rotated or duplicated just to perform
this work. Reuse connected management access first. A value stored in Render
production is not automatically available to this cloud session. The cloud draft
currently declares `RENDER_API_KEY` for `api.render.com` and
`SUPABASE_ACCESS_TOKEN` for `api.supabase.com`; the latest metadata check found
no saved bindings and the runtime found neither variable. Git HTTPS access is
available separately. Do not request GitHub tokens merely because other APIs
are unavailable. Inspect status/names only; never dump environments/key files.

Relevant app settings (names only): `DATABASE_URL` / `SUPABASE_DB_URL`,
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_PROJECT_REF`,
`SUPABASE_REQUIRE_AUTH`, `SMP_ORGANIZATION_ID`, `SMP_BRANCH_CODE`, `JWT_SECRET`,
`SMP_DATA_DIR`, `SMP_BACKUP_DIR`, `SMP_ENV`, `SMP_SCHEDULING_ENABLED`.
`SMP_TEST_POSTGRES_DSN` is exclusively a disposable test DSN.
`SMP_MIGRATION_DATABASE_URL` is secure operator-only migration access, not a CLI
argument. Hosted security startup requires real auth, pinned identity, strong
signing secret, and PostgreSQL TLS verification (`sslmode=verify-full`).

## Verification and promotion

Read [Test/production instructions](docs/TEST_AND_PRODUCTION.md) and the relevant
component UAT guide. Run `python scripts/check_test_environment.py --browser`
from a clean local environment. Use its documented separate PostgreSQL test
cluster to exercise database tests and restore rehearsals. Record skipped and
unrun checks separately; a local test pass is not user UAT approval or a live
production check.

Every release follows: register scope -> implement on test branch -> test exact
candidate -> hosted UAT -> user approval of exact candidate -> backup and
production preflight -> permitted deployment -> live verification -> master
history update. No new user approval exists merely because this process was
requested. See release policy for gates, commands, audit evidence and rollback.

## Remaining security and operations work

The staged fixes remove known login bypasses, restrict static downloads, bind
staff tokens to branch identity and active membership, hash new credentials,
add write-origin checks/login throttling, and tighten branch identity handling.
They are a foundation, not certification. Before production promotion:

- Verify live ownership, current branch ID/code, admin memberships, data counts
  and balances, migration/restore evidence, and every production target.
- Complete real Supabase login/MFA, legacy endpoint authorization, session/XSS,
  database-role/RLS and private-storage reviews. JavaScript session cookies are
  not HttpOnly; the added CSP is not a complete script policy.
- Reset/migrate legacy plaintext credentials through a reviewed process and
  renew old staff sessions. Do not email passwords/PINs or reinstate bypasses.
- Verify protected branches, required reviewers, disabled production auto-deploy
  until controlled promotion is established, encrypted backups, monitoring and
  trusted distributed rate limits. No such platform changes are claimed here.

Older `PROJECT_HANDOFF.md`, deployment notes and component UAT documents may
contain historical assumptions; their statements do not override this master,
current user instructions, or the release-specific evidence/approval record.

## Master change history

This section is generated from `docs/releases/register.json`. Append events with
`scripts/release_guard.py record`; do not edit historical approvals or outcomes.

<!-- RELEASE_HISTORY:START -->
Active release: **SMP-20261107-002**.

### SMP-20261107-001 — Navigation design and Operations tab order

State: **legacy_unverified** · Candidate: `6f2ddc11c8b57085bc6d81700d5373ecf8aa199b`

Teal/indigo/purple layered navigation and Dashboard, Student Roster, Student Barcode, Student Presence, Student Attendance, Reports ordering.

Rollback: Restore the previously reviewed navigation source after explicit production authorization; no database migration was part of this change.

- 2026-11-07T22:47:42.585685Z · **legacy_unverified** · Codex: Source was pushed to production main following the user request. Live deployment and formal release-specific UAT evidence were not verified; no retrospective approval is asserted. Reference: Conversation history and origin/main Git read on 2026-11-07. Source: `6f2ddc11c8b57085bc6d81700d5373ecf8aa199b`.

### SMP-20261107-002 — Branch security, isolated testing and governed production promotion

State: **blocked** · Candidate: `9a9b271d3f05ae8f33192a95f770d64950e4779e`

Staged authentication/branch hardening, CA-YYC-KMN-0001 code-only migration, isolated test workflow, reusable management-credential policy, master documentation and explicit UAT/change-history controls. Production data/configuration remain untouched.

Rollback: Do not promote until gates pass. Before an authorized production rollout retain the prior live source and verified production backup, preserve the branch ID, stop conflicting writes for the migration, and use a reviewed recovery plan that preserves newer writes.

- Check `automated_tests`: **failed** — Local annotated runner passes all 172 tests against PostgreSQL 16. Hosted run 42425086050 still fails Python regressions despite the selected tool version; next run adds visible failure tracebacks. Do not treat local success as hosted success.
- Check `browser`: **pending** — Earlier local Chromium scheduling checks passed at d6129ea251578bf26a3e044ecc217bb25629b105. This candidate changes CI/test diagnostics only; browser checks were not rerun for the new candidate.
- Check `hosted_uat`: **pending** — Hosted TEST service/Supabase project and explicit user UAT approval are not available.
- Check `target_identity`: **pending** — Live Render service/database/branch identities and deployed production revision have not been verified.
- Check `data_protection`: **pending** — Local SQLite and PostgreSQL migration/restore rehearsals passed. Current production backup, ownership inventory and restore evidence are still required.
- Check `security_review`: **blocked** — Real hosted login/MFA, legacy endpoint/session review and database-role/RLS/private-storage verification remain open.
- Check `release_controls`: **blocked** — 13 local policy regressions and real-register validation passed; check-production correctly rejects this unapproved release. Protected main, independent owner reviewers and controlled Render promotion remain unconfigured/unverified.

- 2026-11-07T22:47:42.585685Z · **planned** · Codex: User requested branch isolation, airport-based codes, separate testing, reused authorized management credentials and a master record with explicit UAT approval before production. Reference: Current conversation: security/test environment and master-document requests. Source: `6f0c18473ffa13b110b39db2a40dd25d9b6ba9f2`.
- 2026-11-07T22:47:42.585685Z · **blocked** · Codex: Local baseline passed; production promotion is blocked pending hosted UAT, live security/data verification and provider controls. This request is not production UAT approval. Reference: Local test evidence from staged security baseline and current credential metadata review. Source: `6f0c18473ffa13b110b39db2a40dd25d9b6ba9f2`.
- 2026-11-07T22:51:12.475732Z · **blocked** · Codex: Prepared master/rules, synchronized release history and CI guards. Exact frozen candidate passed 172 Python tests without skips, 15 JavaScript scenarios and browser scheduling checks. Production approval, hosted UAT and provider protection are still absent; production remains unchanged. Reference: Current user request for reusable credentials, master documentation and explicit UAT promotion; local validation log SHA256 fd7fa97d962b14b3a64d53df12f0052cd0d42d063f9579adc27922b96a8c95da. Source: `d6129ea251578bf26a3e044ecc217bb25629b105`.
- 2026-11-07T23:24:08.446309Z · **blocked** · Codex: Investigated earlier staging Python-test failure. Reproduced a restore failure using newer backup tools against PostgreSQL 16: transaction_timeout is unsupported. Pinned CI backup tools to major 16, added an early version check and surfaced restore stderr. All 172 local Python tests and 15 JavaScript checks pass with matching tools. Awaiting hosted rerun; UAT and production gates remain blocked. User-requested local documentation removals are preserved. Reference: GitHub Actions run 42419876363; local PostgreSQL 16 reproduction and validation log SHA256 7a1f4bc356dcfb4aa24ca9a45f8a205d3d772486d03ca02e1a0e7b8465baec74. Source: `65c23647c462f45ac779945be7feef7575178187`.
- 2026-11-07T23:27:09.993710Z · **blocked** · Codex: Documentation validation passed remotely but Python regressions still failed. Added escaped GitHub failure annotations while preserving failing exit status to diagnose the remaining runner difference. Local 172-test run passes; hosted failure remains unresolved. No production changes or local documentation restoration. Reference: GitHub run 42425086050 and local CI reporter validation. Source: `9a9b271d3f05ae8f33192a95f770d64950e4779e`.
<!-- RELEASE_HISTORY:END -->
