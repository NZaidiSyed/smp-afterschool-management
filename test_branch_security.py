"""Security regressions use synthetic temporary databases only."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import app
from branch_security import LoginLimiter, branch_code, isolated_branch, validate_branch_code, validate_production_config
from scripts.migrate_branch_code import inventory, rename
from scripts.allocate_branch_code import allocate


def fixture(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE organizations(id INTEGER PRIMARY KEY);
        INSERT INTO organizations VALUES(7);
        CREATE TABLE branches(id INTEGER PRIMARY KEY, organization_id INTEGER REFERENCES organizations(id), code TEXT UNIQUE);
        INSERT INTO branches VALUES(42,7,'CSC0001');
        CREATE TABLE students(id INTEGER PRIMARY KEY, branch_id INTEGER REFERENCES branches(id), name TEXT);
        INSERT INTO students VALUES(101,42,'Synthetic student');
        CREATE TABLE payments(id INTEGER PRIMARY KEY, student_id INTEGER REFERENCES students(id), branch_id INTEGER REFERENCES branches(id), amount INTEGER);
        INSERT INTO payments VALUES(202,101,42,165);
        CREATE TABLE staff_members(id INTEGER PRIMARY KEY, branch_id INTEGER, active INTEGER);
        INSERT INTO staff_members VALUES(8,42,1);
        CREATE TABLE users(id INTEGER, email TEXT, display_name TEXT, role TEXT, active INTEGER);
    ''')
    conn.commit()
    return conn


class BranchRulesTests(unittest.TestCase):
    def test_concurrent_code_reservations_are_unique_and_never_reassigned(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = Path(directory) / 'registry.sqlite3'
            ids = [str(uuid4()) for _ in range(12)]
            with ThreadPoolExecutor(max_workers=6) as pool:
                codes = list(pool.map(lambda identity: allocate(registry, identity, 'CA', 'YYC'), ids))
            self.assertEqual(len(set(codes)), 12)
            self.assertEqual(set(codes), {f'CA-YYC-KMN-{n:04d}' for n in range(1,13)})
            self.assertEqual(allocate(registry, ids[0], 'CA', 'YYC'), codes[0])
            with self.assertRaises(ValueError):
                allocate(registry, ids[0], 'CA', 'YWG')
            self.assertEqual(allocate(registry, str(uuid4()), 'CA', 'YWG'), 'CA-YWG-KMN-0001')

    def test_login_limiter_expires_and_does_not_trust_forwarded_identity(self):
        now = [0]
        limiter = LoginLimiter(attempts=2, seconds=60, clock=lambda: now[0])
        self.assertTrue(limiter.allow('peer'))
        self.assertTrue(limiter.allow('peer'))
        self.assertFalse(limiter.allow('peer'))
        self.assertTrue(limiter.allow('another-peer'))
        now[0] = 60
        self.assertTrue(limiter.allow('peer'))

    def test_country_airport_and_sequence(self):
        self.assertEqual(branch_code('ca', 'yyc', 1), 'CA-YYC-KMN-0001')
        self.assertEqual(branch_code('CA', 'YYC', 2), 'CA-YYC-KMN-0002')
        self.assertEqual(branch_code('CA', 'YWG', 1), 'CA-YWG-KMN-0001')

    def test_invalid_or_unreviewed_codes_are_rejected(self):
        for value in ['CSC0001', 'CA-CGY-KMN-0001', 'US-YYC-KMN-0001', 'CA-YYC-KMN-0000', 'CA-YYC-KMN-0001\n', 'ca-yyc-kmn-0001']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_branch_code(value)

    def test_counter_overflow_and_boolean_rejected(self):
        for value in [0, 10000, True, '1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                branch_code('CA', 'YYC', value)

    def test_production_config_fails_closed(self):
        settings = dict(DATABASE_URL='postgresql://db.example/test?sslmode=verify-full', SMP_ORGANIZATION_ID='org', SMP_BRANCH_CODE='CA-YYC-KMN-0001',
                        SUPABASE_URL='https://auth.example', SUPABASE_ANON_KEY='configured',
                        JWT_SECRET='x' * 32, SUPABASE_REQUIRE_AUTH=True)
        validate_production_config(settings)
        for key in settings:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_production_config({**settings, key: None})
        with self.assertRaises(RuntimeError):
            validate_production_config({**settings, 'JWT_SECRET': 'short'})


class BranchDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic.sqlite3'
        self.conn = fixture(self.path)
        self.addCleanup(self.conn.close)

    def test_migration_preserves_all_rows_ids_links_and_amounts(self):
        before = inventory(self.conn)
        with self.conn:
            result = rename(self.conn, '42', 'CSC0001', 'CA-YYC-KMN-0001')
        self.assertEqual(result['status'], 'renamed')
        self.assertEqual(inventory(self.conn), before)
        self.assertEqual(self.conn.execute('SELECT branch_id,amount FROM payments').fetchone()[:], (42,165))
        self.assertEqual(isolated_branch(self.conn, expected_code='CA-YYC-KMN-0001')['id'], 42)

    def test_migration_is_idempotent(self):
        with self.conn:
            rename(self.conn, '42', 'CSC0001', 'CA-YYC-KMN-0001')
            result = rename(self.conn, '42', 'CSC0001', 'CA-YYC-KMN-0001')
        self.assertEqual(result['status'], 'already_applied')

    def test_unexpected_related_data_change_rolls_back_rename(self):
        self.conn.execute('''CREATE TRIGGER unexpected_update AFTER UPDATE ON branches
                             BEGIN UPDATE payments SET amount=amount+1; END''')
        self.conn.commit()
        with self.assertRaises(RuntimeError):
            with self.conn:
                rename(self.conn, '42', 'CSC0001', 'CA-YYC-KMN-0001')
        self.assertEqual(self.conn.execute('SELECT code FROM branches').fetchone()[0], 'CSC0001')
        self.assertEqual(self.conn.execute('SELECT amount FROM payments').fetchone()[0],165)

    def test_new_staff_has_branch_ownership_and_no_recoverable_pin(self):
        for column in ('staff_name','role_title','subject','phone','email','hourly_rate','notes','pin','pin_hash','updated_at'):
            self.conn.execute('ALTER TABLE staff_members ADD COLUMN ' + column + ' TEXT')
        with patch.object(app,'PG_MODE',False), patch.object(app,'SMP_BRANCH_CODE',''), patch.object(app,'SMP_ORGANIZATION_ID',''), patch.object(app,'ensure_staff_tables'):
            identity=app.save_staff_member(self.conn,{'staff_name':'Synthetic staff','pin':'458321'})
            row=self.conn.execute('SELECT * FROM staff_members WHERE id=?',(identity,)).fetchone()
            self.assertEqual(row['branch_id'],42)
            self.assertEqual(row['pin'],'')
            self.assertTrue(app.verify_staff_pin('458321',None,row['pin_hash']))
            self.assertNotIn('pin_hash',app.display_staff_member(row))
            self.assertNotIn('pin',app.display_staff_member(row))
            app.save_staff_member(self.conn,{'staff_name':'Renamed synthetic staff','pin':''},identity)
            updated=self.conn.execute('SELECT * FROM staff_members WHERE id=?',(identity,)).fetchone()
            self.assertEqual(updated['pin_hash'],row['pin_hash'])

    def test_wrong_branch_cannot_be_renamed(self):
        with self.assertRaises(ValueError):
            rename(self.conn, '99', 'CSC0001', 'CA-YYC-KMN-0001')
        self.assertEqual(self.conn.execute('SELECT code FROM branches').fetchone()[0], 'CSC0001')

    def test_second_branch_is_rejected_instead_of_selecting_first(self):
        self.conn.execute("INSERT INTO branches VALUES(43,7,'CA-YWG-KMN-0001')")
        with self.assertRaises(RuntimeError):
            isolated_branch(self.conn)

    def test_second_organization_is_rejected(self):
        self.conn.execute('INSERT INTO organizations VALUES(99)')
        with self.assertRaises(RuntimeError):
            isolated_branch(self.conn)

    def test_wrong_configured_identity_is_rejected(self):
        for kwargs in [dict(expected_org='99'), dict(expected_code='CA-YYC-KMN-0001')]:
            with self.assertRaises(RuntimeError):
                isolated_branch(self.conn, **kwargs)

    def test_email_is_not_a_privilege_grant(self):
        with patch.object(app, 'PG_MODE', False), patch.object(app, 'ensure_access_tables'):
            self.assertIsNone(app.get_user_access(self.conn, {'email': 'syedzaidipk@gmail.com'}))
            self.conn.execute("INSERT INTO users VALUES(1,'syedzaidipk@gmail.com','Synthetic','Staff',1)")
            self.assertEqual(app.get_user_access(self.conn, {'email':'syedzaidipk@gmail.com'})['role'], 'Staff')
            self.conn.execute('UPDATE users SET active=0')
            self.assertIsNone(app.get_user_access(self.conn, {'email':'syedzaidipk@gmail.com'}))

    def test_cli_preflight_and_backup_restore(self):
        args = [sys.executable, 'scripts/migrate_branch_code.py', '--sqlite', str(self.path), '--expected-branch-id', '42']
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'preflight_only')
        self.assertEqual(self.conn.execute('SELECT code FROM branches').fetchone()[0], 'CSC0001')
        result = subprocess.run(args + ['--apply', '--backup-dir', str(Path(self.directory.name) / 'backups')],
                                capture_output=True, text=True, check=True)
        report = json.loads(result.stdout)
        with contextlib.closing(sqlite3.connect(report['backup'])) as restored:
            restored.row_factory = sqlite3.Row
            self.assertEqual(restored.execute('SELECT code FROM branches').fetchone()[0], 'CSC0001')
            self.assertEqual(inventory(restored), inventory(self.conn))
        self.assertEqual(self.conn.execute('SELECT code FROM branches').fetchone()[0], 'CA-YYC-KMN-0001')

    def test_staff_token_cannot_cross_branches_or_survive_revocation(self):
        def connect():
            conn = sqlite3.connect(self.path); conn.row_factory = sqlite3.Row; return conn
        with patch.object(app, 'db', side_effect=connect), patch.object(app, 'PG_MODE', False), patch.object(app, 'SMP_BRANCH_CODE', ''), patch.object(app, 'SMP_ORGANIZATION_ID', ''):
            token = app.sign_jwt({'staffId':8})
            self.assertEqual(app.verify_jwt(token)['branch_id'], '42')
            # Same signing key and staff ID, different branch database: still denied.
            self.conn.execute('PRAGMA foreign_keys=OFF')
            self.conn.execute('UPDATE branches SET id=43'); self.conn.commit()
            with self.assertRaises(ValueError): app.verify_jwt(token)
            self.conn.execute('UPDATE branches SET id=42'); self.conn.execute('UPDATE staff_members SET active=0');self.conn.commit()
            with self.assertRaises(ValueError): app.verify_jwt(token)

    def test_production_refuses_plaintext_and_uses_bcrypt(self):
        with patch.object(app, 'PRODUCTION', True):
            self.assertFalse(app.verify_staff_password('password', 'password'))
            self.assertFalse(app.verify_staff_pin('1234', '1234', None))
            hashed = app.hash_bcrypt('a-strong-synthetic-password')
            self.assertTrue(app.verify_staff_password('a-strong-synthetic-password', hashed))
            self.assertFalse(app.verify_staff_password('wrong', hashed))


class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root/'index.html').write_text('<h1>Synthetic test page</h1>')
        (root/'app.py').write_text('PRIVATE SOURCE')
        (root/'production.sqlite3').write_text('PRIVATE DATA')
        (root/'vendor').mkdir();(root/'vendor'/'escape.js').symlink_to('/etc/passwd')
        for name, value in [('ROOT',root),('SUPABASE_REQUIRE_AUTH',True),('PRODUCTION',True)]:
            patcher=patch.object(app,name,value);patcher.start();self.addCleanup(patcher.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self, method, path, headers=None, body=None):
        conn=http.client.HTTPConnection(*self.server.server_address)
        conn.request(method,path,body=body,headers=headers or {})
        response=conn.getresponse();data=response.read();result=(response.status,dict(response.getheaders()),data);conn.close();return result

    def test_only_public_assets_can_be_downloaded_using_get_or_head(self):
        self.assertEqual(self.request('GET','/')[0],200)
        for path in ['/app.py','/production.sqlite3','/.git/config','/requirements.txt','/staffbase-database.json','/vendor/escape.js','/vendor/../staffbase-server.js','/vendor/','/%2e%2e/etc/passwd']:
            for method in ['GET','HEAD']:
                with self.subTest(path=path,method=method):self.assertEqual(self.request(method,path)[0],404)

    def test_mock_login_is_rejected_without_contacting_database(self):
        status,_,_=self.request('GET','/api/bootstrap',{'Authorization':'Bearer mock-token-syedzaidipk@gmail.com'})
        self.assertEqual(status,401)

    def test_anonymous_staff_snapshot_and_management_are_denied(self):
        for method,path in [('GET','/api/data'),('POST','/api/staffbase/teacher-assignments')]:
            with self.subTest(path=path):
                self.assertIn(self.request(method,path,{'Content-Type':'application/json'},'{}' if method=='POST' else None)[0],(401,503))

    def test_cross_origin_writes_are_rejected(self):
        for method in ['POST','PUT','DELETE']:
            self.assertEqual(self.request(method,'/api/students',{'Origin':'https://attacker.example','Content-Type':'application/json'},'{}')[0],403)

    def test_security_headers_and_no_url_token_login(self):
        _,headers,_=self.request('GET','/')
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(headers['Referrer-Policy'],'no-referrer')
        self.assertIn(self.request('GET','/api/bootstrap?token=mock-token-syedzaidipk@gmail.com')[0],(401,503))

    def test_email_get_link_cannot_change_leave_requests(self):
        self.assertEqual(self.request('GET','/api/requests/decide/synthetic/approve')[0],405)


if __name__ == '__main__':
    unittest.main()
