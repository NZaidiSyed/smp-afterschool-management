# Branch isolation and security strategy

## Status and scope

The initial findings below describe the reviewed main-branch source before hardening. Staged changes and their remaining gates are documented in TEST_AND_PRODUCTION.md. Production database contents, deployed configuration, database roles, and active policies have not been inspected. Existing records have NOT been verified or reassigned to CSC0001. This document does not implement isolation. Do not enable a second branch until the migration and isolation tests below pass.

## Required ownership model

Use immutable organization and branch IDs as database keys. The existing branch code CSC0001 must become CA-YYC-KMN-0001, retaining its existing branch ID. Future business codes use country + approved IATA airport + KMN + sequence. Make normalized branch codes globally unique and nonempty. Branch codes identify branches; they never grant access.

Hierarchy: organization -> country -> city -> branch. Store country and city as branch metadata or referenced location records. Geography does not grant access. Any regional or headquarters access requires explicit grants.

Every branch-owned student, enrollment, barcode assignment, attendance record, payment, reconciliation import and row, staff assignment, shift, punch, announcement, attachment, and audit event must have mandatory organization_id and branch_id. Document truly organization-wide records separately and restrict them to organization administrators. A staff identity can be organization-wide, with explicit branch assignments; individual shifts and punches still belong to one branch.

Add database constraints ensuring a branch belongs to its declared organization. Use composite foreign keys so child records cannot reference students, imports, staff assignments, or other parent records belonging to another branch. Scope business uniqueness rules by branch where appropriate. Barcode scans must resolve only within the authorized branch, even if barcode values are globally unique.

## Evidence requiring correction

- app.py current_branch_id selects the first branch rather than an explicit authorized branch.
- app.py ensure_branch_column assigns unowned rows to the first/default branch during startup. Replace with a reviewed, one-time migration; new ambiguous records must fail.
- Branch setup/settings contain UPDATE branches ... WHERE organization_id without a branch predicate. These can update several branches together.
- apply_fee_import writes payments without an explicit branch_id. Audit every creation and update path, not just list queries.
- supabase/schema.sql business policies authorize organization membership/roles, without branch membership predicates.
- Handler.require_auth accepts a mock-token path before enforcing SUPABASE_REQUIRE_AUTH. Remove test authentication from production.
- JWT_SECRET has a known development fallback; production must fail startup if a strong secret is absent. Review token issuance and all staff endpoints for current membership and branch scope.
- Password/PIN helpers contain legacy plaintext comparison and nonadaptive hashing fallbacks. Migrate credentials to a supported adaptive hash without silent fallback; require resets where plaintext may have been stored.

These are source findings, not proof that production configuration is vulnerable in every described way. Treat authentication findings as urgent until deployed behavior is verified.

## Server and database enforcement

Authenticate each request with a verified identity, then resolve active organization/branch membership and required action permission server-side. A browser-selected branch is untrusted input. Reject missing, ambiguous, inactive, or unauthorized branch context; never choose the first branch. Keep scope per request/transaction, never in mutable process-wide branch state.

Centralize scoped reads and writes. Apply ownership checks to record IDs, bulk operations, imports, exports, dashboards, scheduled jobs, staff/mobile APIs, backups, and files. Branch changes require fresh authorization; revoked users must lose access promptly. Return no foreign-branch records or sensitive existence details.

Enforce PostgreSQL row-level security for SELECT/INSERT/UPDATE/DELETE with both USING and WITH CHECK against trusted identity and branch membership. Verify the application database role cannot bypass RLS and is not the table owner/superuser; use FORCE ROW LEVEL SECURITY where appropriate. Keep migration privileges separate. Any pooled request context must be transaction-local and reset safely; clients must not be able to forge that context.

Use private object storage and branch-scoped paths, authorized short-lived downloads, and branch-qualified caches. Remove sensitive offline caching and clear local session state on logout/branch switch. Check service workers and browser storage explicitly.

## Production security requirements

Require production authentication, admin MFA, least-privilege branch roles, strong password hashing, login rate limits, secure session handling, HTTPS, and secret rotation. Remove tokens from query strings; protect cookie-authenticated writes against CSRF and restrict CORS. Never expose database/service-role credentials to browsers. Keep credentials and student data out of logs.

Record actor, organization, branch, action, target, timestamp, and outcome in restricted audit storage. Alert on repeated cross-branch denial and privilege changes. Use encrypted backups with restricted access, tested recovery, retention rules, and branch-aware export authorization. Confirm actual hosting/database encryption settings rather than assuming them.

## CSC0001 migration and rollout

1. Verify production service, database, organization, branch IDs/codes, schema, table counts, active identities, and applied policies using read-only access. Take an encrypted recoverable backup and test restoration in an isolated environment.
2. Confirm the sole real branch is CSC0001. Investigate any CCS001, missing codes, orphaned rows, or inconsistent ownership; do not blindly relabel records based on a default in source code.
3. Build a migration manifest showing row counts and ownership per table. Assign legacy records to the confirmed existing branch ID only where evidence supports it. Quarantine ambiguity for review. Do not delete, reseed, or recreate production data.
4. In staging, remove production auth bypasses, enforce explicit branch scope across every endpoint and job, backfill ownership, then apply mandatory keys, relationship constraints, membership tables, and RLS. Include reconciliation of balances, counts, attendance, and staff schedules before/after.
5. Test the migration on a restored copy. Coordinate writes during the final migration, including background jobs; validate final counts and references before reopening writes. Provide rollback and restore procedures that preserve changes made after rollout.
6. Release the verified single-branch configuration pinned to CSC0001 first. Add another branch only after acceptance criteria pass. Do not claim isolation based on a branch label in the UI.

## Required acceptance evidence

Use two branches in the same city, two in different countries, and separate organizations with synthetic records. Test anonymous users, branch A staff/admin, branch B staff/admin, inactive/revoked memberships, and explicitly authorized headquarters users.

For every API and direct database access path, prove branch A cannot list, read by guessed ID, create, change, delete, import into, export, scan, or download branch B data. Test forged branch codes/IDs, parent-child mismatch, bulk payloads, SQL-level writes, concurrent requests across branches, background jobs, cache reuse, and membership revocation. Denied requests must leave all records unchanged. Check positive same-branch operations and authorized headquarters reporting as well.

Production readiness requires passing isolation tests, reviewed migration results, verified deployed authentication settings and non-bypassing database privileges, backup restoration evidence, and live smoke checks. Record code revision, schema migration version, and test results. No such production readiness claim has been made by this review.
