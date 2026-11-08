"""PostgreSQL migration rehearsals; need a disposable CREATEDB-capable test DSN.

CI supplies an isolated PostgreSQL service. Never configure a production DSN.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

from branch_security import isolated_branch
from scripts.migrate_branch_code import inventory, rename


@unittest.skipUnless(os.environ.get('SMP_TEST_POSTGRES_DSN'), 'Disposable PostgreSQL DSN not configured')
class BranchPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from psycopg.rows import dict_row
        self.sql = sql
        self.admin = psycopg.connect(os.environ['SMP_TEST_POSTGRES_DSN'], autocommit=True)
        self.addCleanup(self.admin.close)
        self.database = 'smp_branch_test_' + uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.database)))
        self.addCleanup(lambda: self.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(self.database))))
        self.dsn = make_conninfo(os.environ['SMP_TEST_POSTGRES_DSN'], dbname=self.database)
        self.conn = psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)
        self.addCleanup(self.conn.close)
        self.org, self.branch, self.student = uuid4(), uuid4(), uuid4()
        self.conn.execute('CREATE TABLE organizations(id uuid PRIMARY KEY)')
        self.conn.execute('CREATE TABLE branches(id uuid PRIMARY KEY, organization_id uuid REFERENCES organizations(id), code text UNIQUE)')
        self.conn.execute('CREATE TABLE students(id uuid PRIMARY KEY, branch_id uuid REFERENCES branches(id), name text)')
        self.conn.execute('CREATE TABLE payments(id uuid PRIMARY KEY, branch_id uuid REFERENCES branches(id), student_id uuid REFERENCES students(id), amount numeric)')
        self.conn.execute('INSERT INTO organizations VALUES(%s)', (self.org,))
        self.conn.execute("INSERT INTO branches VALUES(%s,%s,'CSC0001')", (self.branch,self.org))
        self.conn.execute("INSERT INTO students VALUES(%s,%s,'Synthetic')", (self.student,self.branch))
        self.conn.execute('INSERT INTO payments VALUES(%s,%s,%s,165.25)', (uuid4(),self.branch,self.student))
        self.conn.commit()

    def test_postgres_rename_keeps_ids_and_all_business_records(self):
        before=inventory(self.conn,True)
        with self.conn.transaction():
            report=rename(self.conn,str(self.branch),'CSC0001','CA-YYC-KMN-0001',True)
        self.assertEqual(report['status'],'renamed')
        self.assertEqual(inventory(self.conn,True),before)
        self.assertEqual(str(isolated_branch(self.conn,True,'CA-YYC-KMN-0001')['id']),str(self.branch))
        with self.conn.transaction():
            self.assertEqual(rename(self.conn,str(self.branch),'CSC0001','CA-YYC-KMN-0001',True)['status'],'already_applied')

    def test_postgres_second_branch_is_rejected(self):
        self.conn.execute("INSERT INTO branches VALUES(%s,%s,'CA-YWG-KMN-0001')", (uuid4(),self.org))
        with self.assertRaises(RuntimeError):
            isolated_branch(self.conn,True)

    def test_auth_history_is_scoped_deduplicated_and_paginated(self):
        import auth_audit
        auth_audit.ensure_schema(self.conn,True)
        for scope,reference in [('org:branch','one'),('org:branch','one'),('org:branch','two'),('other:branch','private')]:
            auth_audit.record_verified(self.conn,scope=scope,subject_id='synthetic',login_id='synthetic@example.invalid',
                                      display_name='Synthetic user',role='Admin',method='supabase',
                                      session_reference=reference,pg=True)
        first=auth_audit.page(self.conn,'org:branch',limit=1,pg=True)
        second=auth_audit.page(self.conn,'org:branch',limit=1,cursor=first['next_cursor'],pg=True)
        self.assertEqual(len(first['records'])+len(second['records']),2)
        self.assertNotEqual(first['records'][0]['id'],second['records'][0]['id'])
        self.assertIsNone(second['next_cursor'])
        auth_audit.ensure_schema(self.conn,True)
        self.assertEqual(len(auth_audit.page(self.conn,'org:branch',pg=True)['records']),2)

    def test_auth_history_rls_denies_direct_unprivileged_clients(self):
        import auth_audit
        import psycopg
        auth_audit.ensure_schema(self.conn,True)
        auth_audit.record_verified(self.conn,scope='org:branch',subject_id='synthetic',login_id='synthetic@example.invalid',
                                  display_name='Synthetic user',role='Admin',method='supabase',session_reference='one',pg=True)
        role='smp_audit_reader_'+uuid4().hex
        self.admin.execute(self.sql.SQL('CREATE ROLE {}').format(self.sql.Identifier(role)))
        def cleanup():
            self.conn.execute(self.sql.SQL('REVOKE ALL ON auth_sign_ins FROM {}').format(self.sql.Identifier(role)))
            self.admin.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(role)))
        self.addCleanup(cleanup)
        self.conn.execute(self.sql.SQL('GRANT SELECT,INSERT ON auth_sign_ins TO {}').format(self.sql.Identifier(role)))
        with self.conn.transaction():
            self.conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(role)))
            self.assertEqual(self.conn.execute('SELECT * FROM auth_sign_ins').fetchall(),[])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with self.conn.transaction():
                self.conn.execute(self.sql.SQL('SET LOCAL ROLE {}').format(self.sql.Identifier(role)))
                auth_audit.record_verified(self.conn,scope='org:branch',subject_id='forged',login_id='forged@example.invalid',
                                          display_name='Untrusted',role='Owner',method='supabase',session_reference='forged',pg=True)

    @unittest.skipUnless(shutil.which('pg_dump') and shutil.which('pg_restore'), 'PostgreSQL backup tools unavailable')
    def test_postgres_cli_backup_can_be_restored(self):
        import psycopg
        from psycopg.conninfo import make_conninfo
        from psycopg.rows import dict_row
        import json
        with tempfile.TemporaryDirectory() as directory:
            args=[sys.executable,'scripts/migrate_branch_code.py','--postgres','--expected-branch-id',str(self.branch),
                  '--apply','--backup-dir',directory]
            run=subprocess.run(args,env=dict(os.environ,SMP_MIGRATION_DATABASE_URL=self.dsn),capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            report=json.loads(run.stdout)
            restored_name='smp_restore_test_'+uuid4().hex
            self.admin.execute(self.sql.SQL('CREATE DATABASE {}').format(self.sql.Identifier(restored_name)))
            self.addCleanup(lambda: self.admin.execute(self.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(self.sql.Identifier(restored_name))))
            restored_dsn=make_conninfo(self.dsn,dbname=restored_name)
            from psycopg.conninfo import conninfo_to_dict
            connection_options = conninfo_to_dict(restored_dsn)
            restore_env = dict(os.environ)
            for option, variable in {'host':'PGHOST','hostaddr':'PGHOSTADDR','port':'PGPORT',
                                     'user':'PGUSER','password':'PGPASSWORD','sslmode':'PGSSLMODE',
                                     'sslrootcert':'PGSSLROOTCERT','sslcert':'PGSSLCERT','sslkey':'PGSSLKEY'}.items():
                if option in connection_options:
                    restore_env[variable] = connection_options[option]
            restored_result=subprocess.run(['pg_restore','--no-password','--exit-on-error','--dbname',restored_name,report['backup']],
                                           env=restore_env,capture_output=True,text=True)
            self.assertEqual(restored_result.returncode,0,restored_result.stderr)
            with psycopg.connect(restored_dsn,row_factory=dict_row) as restored:
                self.assertEqual(restored.execute('SELECT code FROM branches').fetchone()['code'],'CSC0001')
                self.assertEqual(inventory(restored,True),inventory(self.conn,True))
