"""Run against a disposable PostgreSQL DB via SMP_TEST_POSTGRES_DSN.

Each test uses its own schema and removes only that schema. Production data is
never needed. The same attendance domain scenarios run on SQLite and Postgres.
"""
import os
import unittest
import uuid
from datetime import timedelta
from attendance import Attendance, ensure_schema
from test_attendance import AttendanceTests as _BaseTests, START, student


@unittest.skipUnless(os.environ.get('SMP_TEST_POSTGRES_DSN'), 'Disposable PostgreSQL DSN not configured')
class PostgresAttendanceTests(_BaseTests):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        self.conn=psycopg.connect(os.environ['SMP_TEST_POSTGRES_DSN'],row_factory=dict_row)
        self.schema='attendance_test_'+uuid.uuid4().hex
        self.conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.schema)))
        ensure_schema(self.conn,pg=True)
        ensure_schema(self.conn,pg=True)  # Additive schema setup is repeatable.
        self.conn.commit()
        self.students=[student()]
        self.counter=0
        self.store=self.at()
        self.code=self.store.assign_barcode('1')

    def at(self,minutes=0,scope='org:branch',students=None):
        return Attendance(self.conn,scope,self.students if students is None else students,
                          pg=True,actor='uat-admin',now=START+timedelta(minutes=minutes))

    def tearDown(self):
        from psycopg import sql
        self.conn.rollback()
        self.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.conn.commit()
        self.conn.close()

    def test_barcode_is_stable_unique_and_backfilled_once(self):
        self.assertEqual(self.code,self.at().assign_barcode('1'))
        other=self.at(students=self.students+[student('2')]).assign_barcode('2')
        self.assertNotEqual(self.code,other)
        row=self.conn.execute("SELECT count(*) AS count FROM attendance_audit WHERE action='assign_barcode'").fetchone()
        self.assertEqual(row['count'],2)

    def test_table_constraints_prevent_multiple_open_visits(self):
        from psycopg.errors import UniqueViolation
        row=self.scan()['record']
        with self.assertRaises(UniqueViolation):
            self.store.q('''INSERT INTO attendance_records
                (id,scope,student_id,lesson_key,day,session_type,status,updated_at)
                VALUES ('duplicate-open',?,?,?,?,'unscheduled','in_center',?)''',
                (self.store.scope,'1','duplicate',row['day'],row['updated_at']))

    def test_attendance_tables_are_server_only(self):
        rows=self.conn.execute('SELECT relname,relrowsecurity FROM pg_class WHERE relnamespace=(SELECT oid FROM pg_namespace WHERE nspname=current_schema()) AND relkind=\'r\'').fetchall()
        self.assertEqual(len(rows),6)
        self.assertTrue(all(r['relrowsecurity'] for r in rows))
        roles=self.conn.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')").fetchall()
        for role in roles:
            row=self.conn.execute('SELECT has_table_privilege(%s,%s,%s) AS allowed',(role['rolname'],self.schema+'.attendance_records','SELECT')).fetchone()
            self.assertFalse(row['allowed'])

    def test_concurrent_scan_retries_return_one_saved_arrival(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from concurrent.futures import ThreadPoolExecutor
        self.conn.commit()
        def scan(_):
            with psycopg.connect(os.environ['SMP_TEST_POSTGRES_DSN'],row_factory=dict_row) as conn:
                conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.schema)))
                store=Attendance(conn,'org:branch',self.students,pg=True,actor='concurrent-test',now=START)
                result=store.scan({'barcode_id':self.code,'request_id':'shared-retry'})
                conn.commit()
                return result
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(scan,range(4)))
        self.assertTrue(all(result==results[0] for result in results))
        self.assertEqual(results[0]['action'],'CHECK_IN')
        self.assertEqual(len(self.at().rows()),1)


# Avoid collecting the imported base class twice in this module.
del _BaseTests

if __name__=='__main__':
    unittest.main()
