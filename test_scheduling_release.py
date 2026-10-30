"""Release integration: retain original rows and overlay only published scoped weeks."""
import json
import sqlite3
import unittest
from unittest.mock import patch
import app
from staff_scheduling import ensure_schema, SchedulingError

class ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        ensure_schema(self.conn)
        self.conn.executescript('''CREATE TABLE staff_members(id INTEGER,branch_id INTEGER,active INTEGER);
          INSERT INTO staff_members VALUES(1,10,1),(2,20,1);
          CREATE TABLE staff_schedules(id INTEGER,staff_id INTEGER,week_start TEXT,weekday TEXT,
            shift_type TEXT,start_time TEXT,end_time TEXT,location TEXT,notes TEXT);
          INSERT INTO staff_schedules VALUES(1,1,'2026-11-02','Tue','Work','15:00','18:00','Room 1','Original'),
            (2,2,'2026-11-02','Tue','Work','15:00','18:00','Other branch','');''')
        self.patches=[patch.object(app,'PG_MODE',False),patch.object(app,'current_org_id',return_value='org'),patch.object(app,'current_branch_id',return_value=10)]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
        self.addCleanup(self.conn.close)

    def test_import_filters_branch_preserves_original_and_uses_stable_ids(self):
        draft=app.scheduler_legacy_draft(self.conn,'2026-11-02')
        self.assertEqual(len(draft),1);self.assertEqual(draft[0]['day'],'2026-11-03')
        self.assertEqual(draft,app.scheduler_legacy_draft(self.conn,'2026-11-02'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM staff_schedules').fetchone()[0],2)

    def test_published_overlay_preserves_other_branch_and_all_daily_shifts(self):
        legacy=list(self.conn.execute('SELECT * FROM staff_schedules'))
        self.assertEqual(app.scheduler_published_rows(self.conn,'2026-11-02',legacy),legacy)
        draft=app.scheduler_legacy_draft(self.conn,'2026-11-02');draft.append({**draft[0],'id':'second','start':'19:00','end':'20:00'})
        self.conn.execute("INSERT INTO scheduler_weeks(scope,week_start,published_json,published_revision) VALUES('org:10','2026-11-02',?,1)",(json.dumps(draft),))
        overlay=app.scheduler_published_rows(self.conn,'2026-11-02',legacy)
        self.assertEqual(len(overlay),3);self.assertEqual(overlay[0]['staff_id'],2)
        own=app.scheduler_published_rows(self.conn,'2026-11-02',[legacy[0]],staff_id=1)
        self.assertEqual(len(own),2);self.assertEqual(own[1]['start_time'],'19:00')
        self.conn.execute("UPDATE scheduler_weeks SET published_json='[]'")
        self.assertEqual(app.scheduler_published_rows(self.conn,'2026-11-02',[legacy[0]],1),[])

    def test_invalid_legacy_weekday_is_not_silently_discarded(self):
        self.conn.execute("UPDATE staff_schedules SET weekday='invalid' WHERE id=1")
        with self.assertRaises(SchedulingError):app.scheduler_legacy_draft(self.conn,'2026-11-02')
