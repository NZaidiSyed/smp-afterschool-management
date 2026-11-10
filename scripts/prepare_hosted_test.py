"""Prepare an EMPTY, explicitly selected Supabase TEST database.

Read-only preflight by default. Supply the operator connection only through
SMP_TEST_BOOTSTRAP_DATABASE_URL. Never use the local SQLite UAT seeders here.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from datetime import date
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def validate_target(settings, expected_ref):
    if settings.get('SMP_ENV') != 'staging':
        raise ValueError('SMP_ENV must explicitly be staging')
    if not re.fullmatch(r'[a-z]{20}', expected_ref or ''):
        raise ValueError('Expected Supabase project reference must contain 20 lowercase letters')
    if expected_ref == 'ccftzopeneykbcwkwizq':
        raise ValueError('The historical production project is forbidden')
    if settings.get('SMP_TEST_PROJECT_REF') != expected_ref:
        raise ValueError('SMP_TEST_PROJECT_REF must match the independently selected TEST project')
    if settings.get('DATABASE_URL') or settings.get('SUPABASE_DB_URL'):
        raise ValueError('Use a clean operator process without application database bindings')
    dsn = settings.get('SMP_TEST_BOOTSTRAP_DATABASE_URL', '')
    parsed = urlparse(dsn)
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname:
        raise ValueError('A TEST PostgreSQL URI is required through the secure environment')
    if parsed.path != '/postgres' or parsed.port not in (None, 5432):
        raise ValueError('Use the TEST postgres database and a direct or session-pooler connection on port 5432')
    options = parse_qs(parsed.query)
    if options.get('sslmode') != ['verify-full']:
        raise ValueError('TEST PostgreSQL requires sslmode=verify-full')
    # Do not allow query parameters to override the independently checked host.
    if set(options) - {'sslmode', 'sslrootcert', 'connect_timeout'}:
        raise ValueError('Unsupported connection query options')
    direct = parsed.hostname == f'db.{expected_ref}.supabase.co'
    pooler = (parsed.hostname.endswith('.pooler.supabase.com') and
              unquote(parsed.username or '').endswith('.' + expected_ref))
    if not (direct or pooler):
        raise ValueError('Database endpoint does not identify the selected TEST project')
    return dsn


def preflight(conn, admin_email):
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
    if tables:
        raise ValueError('TEST bootstrap requires an empty public schema; existing databases are never reseeded')
    users = conn.execute('''SELECT id, email, email_confirmed_at FROM auth.users
                            WHERE lower(email)=lower(%s)''', (admin_email,)).fetchall()
    if len(users) != 1 or not users[0]['email_confirmed_at']:
        raise ValueError('Create exactly one confirmed TEST Auth user matching the administrator email first')
    return users[0]


class AtomicConnection:
    """Keep legacy schema helpers inside the operator's one transaction."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def commit(self):
        pass

    def rollback(self):
        raise RuntimeError('Schema initialization failed; roll back the entire TEST bootstrap')


def initialize(conn, app, admin):
    """Called only after locked empty-schema and confirmed-user checks."""
    from psycopg import sql
    organization_id, branch_id = uuid4(), uuid4()
    conn.execute((ROOT / 'supabase/schema.sql').read_text())
    conn.execute('''CREATE TABLE public.branches (
        id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES public.organizations(id),
        name text NOT NULL, slug text NOT NULL, code text NOT NULL UNIQUE,
        settings jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())''')
    month = date.today().strftime('%b-%y')
    conn.execute("INSERT INTO organizations(id,name,current_month) VALUES(%s,'SMP Synthetic TEST Centre',%s)", (organization_id, month))
    conn.execute('''INSERT INTO branches(id,organization_id,name,slug,code,settings)
        VALUES(%s,%s,'Synthetic Calgary TEST','synthetic-test','CA-YYC-KMN-0001',
        '{"timezone":"America/Edmonton","currency":"CAD"}')''', (branch_id, organization_id))
    # Importing the app is confined to the operator process. Pin its helpers to
    # this new identity; no change to normal application startup is required.
    app.SMP_ORGANIZATION_ID = str(organization_id)
    app.SMP_BRANCH_CODE = 'CA-YYC-KMN-0001'
    atomic = AtomicConnection(conn)
    for helper in (app.ensure_access_tables, app.ensure_status_change_table,
                   app.ensure_student_audit_tables, app.ensure_student_schedule_tables,
                   app.ensure_staff_tables):
        helper(atomic)
    conn.execute('''CREATE TABLE public.monthly_expenses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id uuid NOT NULL REFERENCES organizations(id), month_label varchar(10) NOT NULL,
        rent_expense numeric(10,2) DEFAULT 0, royalty_expense numeric(10,2) DEFAULT 0,
        utilities_expense numeric(10,2) DEFAULT 0, misc_expense numeric(10,2) DEFAULT 0,
        misc_details text, created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now(),
        UNIQUE(organization_id,month_label))''')
    app.auth_audit.ensure_schema(atomic, True)
    app.ensure_attendance_schema(atomic, True)
    app.ensure_scheduler_schema(atomic, True)
    conn.execute('''INSERT INTO app_users(organization_id,branch_id,email,display_name,role,active)
        VALUES(%s,%s,%s,'TEST Administrator','Admin',true)''',
                 (organization_id, branch_id, admin['email']))
    conn.execute('''INSERT INTO organization_members(organization_id,user_id,role)
                    VALUES(%s,%s,'admin')''', (organization_id, admin['id']))
    conn.execute('''INSERT INTO app_meta(key,value) VALUES
        ('smp_test_project','synthetic-only'), ('center_setup_completed','1')''')
    for number, name in enumerate(('Synthetic TEST Alex', 'Synthetic TEST Morgan', 'Synthetic TEST Casey'), 1):
        student = conn.execute('''INSERT INTO students(organization_id,branch_id,number,
            student_name,parent_guardian,enrol_date,subjects,rate_type,std_monthly_fee)
            VALUES(%s,%s,%s,%s,'Synthetic TEST Guardian',current_date,ARRAY['Math'],'R',165)
            RETURNING id''', (organization_id, branch_id, number, name)).fetchone()
        if number == 1:
            conn.execute('''INSERT INTO payments(organization_id,branch_id,student_id,month_label,
                amount,payment_verified,payment_source,transaction_reference)
                VALUES(%s,%s,%s,%s,165,true,'Synthetic TEST','synthetic-test-payment')''',
                         (organization_id, branch_id, student['id'], month))
    # No browser client needs direct database access: use authenticated SMP APIs.
    # Apply after *all* helper-created tables, not just the historical starter SQL.
    roles = [r['rolname'] for r in conn.execute(
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role')").fetchall()]
    for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall():
        table = sql.Identifier('public', row['tablename'])
        conn.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(table))
        conn.execute(sql.SQL('REVOKE ALL ON TABLE {} FROM PUBLIC').format(table))
        for role in roles:
            conn.execute(sql.SQL('REVOKE ALL ON TABLE {} FROM {}').format(table, sql.Identifier(role)))
    for role in ['PUBLIC'] + roles:
        identity = sql.SQL('PUBLIC') if role == 'PUBLIC' else sql.Identifier(role)
        conn.execute(sql.SQL('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}').format(identity))
        conn.execute(sql.SQL('REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {}').format(identity))
    app.isolated_branch(conn, True, 'CA-YYC-KMN-0001', str(organization_id))
    return {'organization_id': str(organization_id), 'branch_id': str(branch_id),
            'branch_code': 'CA-YYC-KMN-0001', 'synthetic_only': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-project-ref', required=True)
    parser.add_argument('--admin-email', required=True, help='Confirmed TEST Auth user; never a password')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    dsn = validate_target(os.environ, args.expected_project_ref)
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=15) as conn:
        conn.execute("SET LOCAL lock_timeout='10s'")
        conn.execute("SET LOCAL statement_timeout='60s'")
        # Read-only preflight does not acquire a write lock or execute DDL.
        if not args.apply:
            conn.execute('SET TRANSACTION READ ONLY')
            preflight(conn, args.admin_email)
            print(json.dumps({'preflight': 'passed', 'project_ref': args.expected_project_ref,
                              'applied': False}))
            return
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('smp-empty-test-bootstrap'))")
        admin = preflight(conn, args.admin_email)
        os.environ['DATABASE_URL'] = dsn
        os.environ.setdefault('SMP_DATA_DIR', '/tmp/smp-hosted-test-operator')
        import app
        result = initialize(conn, app, admin)
    print(json.dumps({'applied': True, 'project_ref': args.expected_project_ref, **result}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Database exceptions can include SQL and connection details. Keep those
        # out of terminal logs; validation errors contain only our fixed text.
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        print('TEST preparation failed: ' + detail, file=sys.stderr)
        sys.exit(1)
