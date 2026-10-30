"""Repeatable synthetic fixtures for a dedicated local UAT database."""
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from attendance import ensure_schema, stamp


def main():
    if app.PG_MODE or app.SUPABASE_REQUIRE_AUTH or 'uat' not in str(app.DATA_DIR).lower():
        raise SystemExit('Synthetic fixtures require an explicit UAT SQLite data directory and local authentication.')
    if app.WORKBOOK.exists():
        raise SystemExit('Use the supplied clean UAT source folder without a student workbook.')
    app.init_db()
    with app.db() as conn:
        app.ensure_staff_tables(conn)
    app.ensure_meta_defaults()
    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('America/Edmonton'))
    with app.db() as conn:
        ensure_schema(conn)
        for key,value in [('center_setup_completed','1'), ('institution_name','Attendance UAT Centre')]:
            conn.execute('INSERT INTO app_meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,value))
        conn.execute("UPDATE organizations SET name='Attendance UAT Centre' WHERE id=(SELECT organization_id FROM branches WHERE id=?)", (app.current_branch_id(conn),))
        conn.execute("UPDATE branches SET name='UAT Branch', code='UAT' WHERE id=?", (app.current_branch_id(conn),))
        schedule_day = local.strftime('%A') if local.strftime('%A') in app.STUDENT_SCHEDULE_WEEKDAYS else 'Tuesday'
        for name, schedule in [('UAT Alex Regular',True),('UAT Morgan Makeup',False),('UAT Casey Unscheduled',False)]:
            existing = conn.execute('SELECT id FROM students WHERE student_name=?', (name,)).fetchone()
            if existing:
                if schedule:
                    app.save_student_schedules(conn, existing['id'], [{'weekday':schedule_day,'start_time':'15:00','end_time':'20:00'}])
                continue
            payload = {'student_name':name,'parent_guardian':'Synthetic UAT Parent','status':'C',
                       'enrol_date':local.date().isoformat(),'subjects':'Math','rate_type':'R',
                       'schedules':[{'weekday':schedule_day,'start_time':'15:00','end_time':'20:00'}] if schedule else []}
            app.insert_student_record(conn, app.normalize_student(payload), app.next_student_number(conn), 'uat-fixture')
        store = app.attendance_store(conn, 'uat-fixture')
        if local.strftime('%A') not in app.STUDENT_SCHEDULE_WEEKDAYS:
            # The existing roster supports Tue–Sat. Supply an explicit synthetic
            # lesson so that regular-scan UAT also works on Sunday and Monday.
            alex = next(s for s in store.students if s['student_name']=='UAT Alex Regular')
            day = local.date().isoformat()
            store.q('''INSERT INTO attendance_records
                (id,scope,student_id,lesson_key,day,planned_start,planned_end,session_type,status,note,updated_at)
                VALUES (?,?,?,?,?,?,?,'regular','expected','Synthetic closed-day UAT lesson',?)
                ON CONFLICT(scope,student_id,lesson_key) DO NOTHING''',
                ('uat-alex-'+day,store.scope,str(alex['id']),'synthetic-regular:'+day,day,
                 stamp(local.replace(hour=15,minute=0,second=0,microsecond=0)),
                 stamp(local.replace(hour=20,minute=0,second=0,microsecond=0)),stamp(now)))
        # One clearly synthetic prior absence exercises automatic makeup linking.
        morgan = next(s for s in store.students if s['student_name']=='UAT Morgan Makeup')
        day = (local.date()-timedelta(days=1)).isoformat()
        key='synthetic-missed-lesson'
        store.q('''INSERT INTO attendance_records
            (id,scope,student_id,lesson_key,day,planned_start,planned_end,session_type,status,note,updated_at)
            VALUES (?,?,?,?,?,?,?,'regular','absent','Synthetic UAT absence',?)
            ON CONFLICT(scope,student_id,lesson_key) DO NOTHING''',
            ('uat-morgan-absence',store.scope,str(morgan['id']),key,day,stamp(now-timedelta(days=1,hours=1)),stamp(now-timedelta(days=1)),stamp(now)))
        conn.commit()
    print('Prepared regular, makeup and unscheduled synthetic students. Existing UAT data/barcodes preserved.')


if __name__ == '__main__':
    main()
