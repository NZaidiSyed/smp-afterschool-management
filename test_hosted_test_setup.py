"""TEST bootstrap guards and real PostgreSQL schema/startup rehearsals."""
import os
import http.client
import io
import json
import threading
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.prepare_hosted_test import initialize, preflight, validate_target


class HostedTargetTests(unittest.TestCase):
    def settings(self):
        ref = 'abcdefghijklmnopqrst'
        return ref, {'SMP_ENV': 'staging', 'SMP_TEST_PROJECT_REF': ref,
                     'SMP_TEST_BOOTSTRAP_DATABASE_URL':
                         f'postgresql://postgres:synthetic@db.{ref}.supabase.co/postgres?sslmode=verify-full'}

    def test_target_requires_staging_matching_project_and_verified_tls(self):
        ref, settings = self.settings()
        self.assertTrue(validate_target(settings, ref))
        for update in ({'SMP_ENV': 'production'}, {'SMP_TEST_PROJECT_REF': 'z' * 20},
                       {'DATABASE_URL': 'configured'}, {'SUPABASE_DB_URL': 'configured'},
                       {'SMP_TEST_BOOTSTRAP_DATABASE_URL': settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'].replace('verify-full', 'require')}):
            with self.subTest(update=list(update)), self.assertRaises(ValueError):
                validate_target({**settings, **update}, ref)

    def test_wrong_host_production_and_query_overrides_are_rejected(self):
        ref, settings = self.settings()
        for uri in (settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'].replace(ref, 'z' * 20),
                    settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'] + '&host=production.invalid',
                    settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'] + '&sslmode=disable'):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                validate_target({**settings, 'SMP_TEST_BOOTSTRAP_DATABASE_URL': uri}, ref)
        with self.assertRaises(ValueError):
            validate_target(settings, 'ccftzopeneykbcwkwizq')

    def test_pooler_requires_the_selected_project_username(self):
        ref, settings = self.settings()
        settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'] = (
            f'postgresql://postgres.{ref}:synthetic@aws-0-ca-central-1.pooler.supabase.com:5432/postgres?sslmode=verify-full')
        self.assertTrue(validate_target(settings, ref))
        settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'] = settings['SMP_TEST_BOOTSTRAP_DATABASE_URL'].replace('postgres.' + ref, 'postgres.other')
        with self.assertRaises(ValueError):
            validate_target(settings, ref)


@unittest.skipUnless(os.environ.get('SMP_TEST_POSTGRES_DSN'), 'Disposable PostgreSQL DSN not configured')
class HostedBootstrapPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from psycopg.rows import dict_row
        import app
        self.app = app
        self.admin = psycopg.connect(os.environ['SMP_TEST_POSTGRES_DSN'], autocommit=True)
        self.addCleanup(self.admin.close)
        self.database = 'smp_hosted_setup_test_' + uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.database)))
        self.addCleanup(lambda: self.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(self.database))))
        self.dsn = make_conninfo(os.environ['SMP_TEST_POSTGRES_DSN'], dbname=self.database)
        self.conn = psycopg.connect(self.dsn, row_factory=dict_row)
        self.addCleanup(self.conn.close)
        # Local provider-schema stub; never represent this as real Supabase UAT.
        self.user_id = uuid4()
        self.conn.execute('''CREATE SCHEMA auth;
            CREATE TABLE auth.users(id uuid PRIMARY KEY,email text,email_confirmed_at timestamptz);
            CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS $$ SELECT NULL::uuid $$;''')
        self.conn.execute("INSERT INTO auth.users VALUES(%s,'admin@example.invalid',now())", (self.user_id,))
        self.conn.commit()
        for name, value in [('PG_MODE', True), ('PRODUCTION', True), ('SMP_ORGANIZATION_ID', ''),
                            ('SMP_BRANCH_CODE', 'CA-YYC-KMN-0001'), ('DATABASE_URL', self.dsn)]:
            context = patch.object(app, name, value)
            context.start()
            self.addCleanup(context.stop)

    def test_empty_database_bootstrap_supports_repeatable_app_startup(self):
        admin = preflight(self.conn, 'admin@example.invalid')
        result = initialize(self.conn, self.app, admin)
        self.conn.commit()
        self.assertEqual(result['branch_code'], 'CA-YYC-KMN-0001')
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM app_users').fetchone()['n'], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM students').fetchone()['n'], 3)
        self.assertEqual(self.conn.execute('SELECT sum(amount) AS total FROM payments').fetchone()['total'], 165)
        self.assertEqual(self.conn.execute('''SELECT count(*) AS n FROM pg_tables
            WHERE schemaname='public' AND NOT rowsecurity''').fetchone()['n'], 0)
        self.conn.commit()
        # Exercise the actual startup defaults twice; no DB reset or reseeding.
        self.app.ensure_pg_defaults()
        self.app.ensure_pg_defaults()
        with self.app.db() as connection:
            self.assertEqual(str(self.app.current_branch_id(connection)), result['branch_id'])
            self.assertEqual(self.app.get_user_access(connection, {'email': 'admin@example.invalid'})['role'], 'Admin')
        self.conn.rollback()
        with self.assertRaises(ValueError):
            preflight(self.conn, 'admin@example.invalid')

    def test_browser_database_role_cannot_read_synthetic_business_or_audit_data(self):
        from psycopg import sql
        from psycopg.errors import InsufficientPrivilege
        role = 'smp_test_browser_' + uuid4().hex
        self.admin.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
        self.addCleanup(lambda: self.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role))))
        admin = preflight(self.conn, 'admin@example.invalid')
        initialize(self.conn, self.app, admin)
        self.conn.commit()
        for table in ('students', 'payments', 'app_users', 'auth_sign_ins', 'branches'):
            with self.subTest(table=table):
                with self.assertRaises(InsufficientPrivilege):
                    with self.conn.transaction():
                        self.conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
                        self.conn.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table)))

    def test_authenticated_postgres_bootstrap_and_private_history_http(self):
        from test_auth_audit import fake_token
        admin = preflight(self.conn, 'admin@example.invalid')
        initialize(self.conn, self.app, admin)
        self.conn.commit()
        self.app.ensure_pg_defaults()
        # Real application DB/API, stubbed provider only. Hosted login is still UAT.
        token = fake_token(str(self.user_id), 'test-session', 'test-nonce')
        provider = {'id': str(self.user_id), 'email': 'admin@example.invalid',
                    'email_confirmed_at': '2026-01-01T00:00:00Z', 'user_metadata': {}}
        def verify_provider(request, **kwargs):
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + token)
            response = io.BytesIO(json.dumps(provider).encode())
            response.status = 200
            return response
        with patch.object(self.app, 'SUPABASE_REQUIRE_AUTH', True), \
             patch.object(self.app, 'SUPABASE_URL', 'https://synthetic-auth.invalid'), \
             patch.object(self.app, 'SUPABASE_ANON_KEY', 'synthetic-public-key'), \
             patch.object(self.app, 'urlopen', side_effect=verify_provider), \
             patch.object(self.app.Handler, 'log_message'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), self.app.Handler)
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            for path in ('/api/bootstrap', '/api/auth/history'):
                request = http.client.HTTPConnection(*server.server_address, timeout=15)
                try:
                    request.request('GET', path, headers={'Authorization': 'Bearer ' + token})
                    response = request.getresponse()
                    payload = json.loads(response.read())
                    self.assertEqual(response.status, 200, payload)
                    if path == '/api/bootstrap':
                        self.assertEqual(len(payload['students']), 3)
                        self.assertEqual(payload['settings']['branch_code'], 'CA-YYC-KMN-0001')
                    else:
                        self.assertEqual(len(payload['records']), 1)
                finally:
                    request.close()

    def test_existing_database_or_unconfirmed_user_is_refused_before_writes(self):
        self.conn.execute('UPDATE auth.users SET email_confirmed_at=NULL')
        with self.assertRaises(ValueError):
            preflight(self.conn, 'admin@example.invalid')
        self.conn.execute('CREATE TABLE public.existing_data(id int)')
        with self.assertRaises(ValueError):
            preflight(self.conn, 'admin@example.invalid')
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'").fetchone()['n'], 1)

    def test_failure_rolls_back_schema_and_preserves_auth(self):
        admin = preflight(self.conn, 'admin@example.invalid')
        self.conn.rollback()
        with self.assertRaises(RuntimeError):
            with self.conn.transaction():
                initialize(self.conn, self.app, admin)
                raise RuntimeError('Synthetic operator failure')
        self.assertEqual(self.conn.execute("SELECT count(*) AS n FROM pg_tables WHERE schemaname='public'").fetchone()['n'], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) AS n FROM auth.users').fetchone()['n'], 1)


if __name__ == '__main__':
    unittest.main()
