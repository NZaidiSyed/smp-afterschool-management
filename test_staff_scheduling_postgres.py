"""Run scheduling scenarios on a disposable Postgres database in isolated schemas."""
import os
import unittest
import uuid
from datetime import datetime,timezone
from staff_scheduling import Scheduler,ensure_schema
from test_staff_scheduling import SchedulingTests as _Base,STAFF


@unittest.skipUnless(os.environ.get('SMP_TEST_POSTGRES_DSN'),'Disposable Postgres DSN not configured')
class PostgresSchedulingTests(_Base):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        self.conn=psycopg.connect(os.environ['SMP_TEST_POSTGRES_DSN'],row_factory=dict_row)
        self.schema='scheduler_test_'+uuid.uuid4().hex
        self.conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.schema)))
        ensure_schema(self.conn,True);ensure_schema(self.conn,True);self.conn.commit()
        self.store=self.as_user()
        for s in STAFF:
            self.store.availability({'staff_id':s['id'],'effective_from':'2026-01-01','revision':0,'windows':{str(i):[{'start':'13:00','end':'21:00'}] for i in range(7)}})

    def as_user(self,sid=None,manager=True,scope='org:branch',leave=None):
        return Scheduler(self.conn,scope,STAFF,'test-manager' if manager else sid,staff_id=sid,manager=manager,leave=leave,pg=True,now=datetime(2026,10,29,20,tzinfo=timezone.utc))

    def tearDown(self):
        from psycopg import sql
        self.conn.rollback();self.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)));self.conn.commit();self.conn.close()

    def test_new_tables_are_server_only(self):
        rows=self.conn.execute("SELECT relrowsecurity FROM pg_class WHERE relnamespace=(SELECT oid FROM pg_namespace WHERE nspname=current_schema()) AND relkind='r'").fetchall()
        self.assertEqual(len(rows),6);self.assertTrue(all(r['relrowsecurity'] for r in rows))

    def test_concurrent_editors_cannot_overwrite_each_other(self):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from concurrent.futures import ThreadPoolExecutor
        from staff_scheduling import SchedulingConflict
        self.store.week('2026-11-02');self.conn.commit()
        def save(editor):
            with psycopg.connect(os.environ['SMP_TEST_POSTGRES_DSN'],row_factory=dict_row) as conn:
                conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.schema)))
                store=Scheduler(conn,'org:branch',STAFF,editor,manager=True,pg=True)
                try:
                    store.save({'week_start':'2026-11-02','revision':0,'shifts':[self.shift(shift_id=editor)]})
                    conn.commit()
                    return 'saved'
                except SchedulingConflict:
                    conn.rollback()
                    return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(save,['editor-one','editor-two']))
        self.assertEqual(sorted(results),['conflict','saved'])


del _Base
if __name__=='__main__':unittest.main()
