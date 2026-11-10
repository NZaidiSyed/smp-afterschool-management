# Hosted TEST preparation

Read APP_MASTER.md and RELEASE_POLICY.md first. This procedure creates new TEST
resources only; it does not authorize production deployment or a production
migration. Use the staging branch and freeze/record its exact candidate before
deploying TEST. Existing production resources must stay unchanged.

## Access and project selection

Use secure operator bindings for RENDER_API_KEY (api.render.com) and
SUPABASE_ACCESS_TOKEN (api.supabase.com). Never put those management credentials
in the application service. Select the owner's actual Render workspace and
Supabase organization from account metadata; stop on ambiguity. Creating a paid
resource must remain within the owner's authorized account and billing limits.

Create one new Supabase project, named `smp-uat`, and a new Render service named
`smp-afterschool-management-test`. Do not import production data or app secrets.
Record the new project/service IDs and resource region. Keep Render auto-deploy
disabled. The historical project `ccftzopeneykbcwkwizq` is forbidden for TEST.

In Supabase Authentication, create a dedicated TEST administrator using an email
the tester controls. Confirm that account through the provider's supported flow.
Do not reuse a live user's password or copy authentication records. Add the
eventual Render TEST HTTPS address as the Site URL and explicitly permitted
authentication redirects. Keep live mail/payment integrations disconnected.

## Database bootstrap

`supabase/schema.sql` alone is historical starter SQL, not a complete hosted
bootstrap. `scripts/prepare_hosted_test.py` installs that schema plus the current
branch, access, attendance, scheduling and audit tables in one transaction. It
adds exactly one synthetic organization/branch, a matching confirmed Admin
membership, three synthetic students and one synthetic $165 payment. It enables
RLS on every public application table and revokes direct browser-role access.

Use an operator Python 3.12 environment with requirements.txt installed. Supply
these settings securely, without putting connection strings in shell history:

- `SMP_ENV=staging`
- `SMP_TEST_PROJECT_REF`: the independently verified new TEST project reference
- `SMP_TEST_BOOTSTRAP_DATABASE_URL`: the new TEST PostgreSQL connection URI
- `PGSSLROOTCERT`: provider-trusted CA path if the server chain needs one

The connection must target `/postgres`, use a direct or session-pooler endpoint
on port 5432, and specify `sslmode=verify-full`. Transaction pooling is not the
bootstrap path. Download CA certificates from the provider's authenticated
settings/official source; never disable verification. Use a clean operator
process without DATABASE_URL or SUPABASE_DB_URL. The TEST URI is operator access,
not a proxy credential placeholder: libpq needs actual secure database access.

From `/workspace/smp-afterschool-management`, first run the read-only preflight:

```sh
python scripts/prepare_hosted_test.py --expected-project-ref TEST_PROJECT_REF --admin-email TEST_ADMIN_EMAIL
```

After verifying the empty TEST target, run the same command with `--apply`.
Only non-secret organization/branch IDs and outcome are printed. Copy the
resulting organization UUID into Render's SMP_ORGANIZATION_ID. Rerunning `--apply`
on a prepared database deliberately fails; it never resets or reseeds data.
Schema failure rolls back the whole bootstrap, leaving Auth users unchanged.

The script refuses any existing public tables. If Supabase created optional
extension tables there, investigate them; do not delete tables to bypass the
guard. Use a fresh compatible TEST project or a separately reviewed migration.
It does not create Auth users or make external requests to send invitations.

## Render TEST configuration

Create a NEW Python web service from this repository using
`security/branch-isolation-staging`, or select the `render.test.yaml` Blueprint
path explicitly. Do not apply `render.yaml` to the existing service.

| Setting | Value |
| --- | --- |
| Python | 3.12.14 |
| Build | `pip install -r requirements.txt` |
| Start | `python app.py` |
| Health check | `/api/healthz` |
| Auto-deploy | Off |
| SMP_ENV | staging |
| SMP_DATA_DIR | /tmp/smp-test |
| SMP_BACKUP_DIR | /tmp/smp-test/backups |
| SMP_BRANCH_CODE | CA-YYC-KMN-0001 |
| SUPABASE_REQUIRE_AUTH | 1 |
| SMP_ORGANIZATION_ID | New synthetic organization UUID |
| DATABASE_URL | TEST connection URI with sslmode=verify-full |
| SUPABASE_URL | New TEST project HTTPS URL |
| SUPABASE_PROJECT_REF | New TEST project reference |
| SUPABASE_ANON_KEY | TEST project's browser anon key |
| JWT_SECRET | Distinct securely generated secret, minimum 32 bytes |

Configure a trusted CA file/PGSSLROOTCERT in Render if needed. Temporary local
files are ephemeral; PostgreSQL holds TEST business data. Backups and restore
evidence must be retained in restricted persistent storage outside /tmp.

The application still performs DDL during startup and some reads. A restricted
non-owner backend role is **not proven compatible** by this bootstrap. Do not
claim least-privilege production readiness or grant browser roles table access
to work around startup. Separating versioned migrations from runtime DDL and
verifying a restricted backend role remain security work. The initial isolated
TEST operator connection must never become a production connection.

## UAT and retained evidence

Record service/project identities, full deployed commit, deployment reference,
health-check result, and test outcomes without credentials or personal data.
Check real provider login, confirmation, logout, expiry/revocation and denied
access. Provision distinct TEST staff/manager accounts through the reviewed app
flow; set credentials through secure reset flows, never shared fixtures.

Run the component checklists for students, attendance/barcodes and scheduling,
and representative finance workflows. Check Admin Sign-in History, ordinary-role
denial, branch identity, desktop/mobile behavior, and browser/API errors. Verify
direct browser database access is denied, actual RLS/role privileges and private
storage. Rehearse TEST backup/restore and code-only migration on disposable TEST
data, retaining counts and balances. Real provider MFA enforcement, legacy
endpoint authorization, sessions/XSS and production controls remain explicit
gates; a local provider stub or a green health check cannot satisfy them.

Local reproduction uses the existing checkout (no worktree):

```sh
python scripts/check_test_environment.py --browser --port 8798
```

Set SMP_TEST_POSTGRES_DSN only to a disposable local database and select matching
pg_dump/pg_restore tools to include PostgreSQL tests. Hosted UAT and human review
are separate from this automated local run. Update release records with actual
evidence; no script can approve production on the user's behalf.
