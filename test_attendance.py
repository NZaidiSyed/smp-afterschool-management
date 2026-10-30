"""Attendance domain regression tests, independent of production credentials."""
import copy
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from attendance import Attendance, ensure_schema, parse_centre_time

UTC = timezone.utc
START = datetime(2026, 10, 29, 22, 0, tzinfo=UTC)  # Thursday 16:00 in Edmonton


def student(student_id='1', name='UAT Student', schedules=True):
    return {'id': student_id, 'student_name': name, 'status': 'C', 'enrol_date': '2026-10-01',
            'schedules': [{'weekday': 'Thursday', 'start_time': '16:00', 'end_time': '17:00'}] if schedules else []}


class AttendanceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        ensure_schema(self.conn)
        self.students = [student()]
        self.counter = 0
        self.store = self.at()
        self.code = self.store.assign_barcode('1')

    def tearDown(self):
        self.conn.close()

    def at(self, minutes=0, scope='org:branch', students=None):
        return Attendance(self.conn, scope, self.students if students is None else students,
                          actor='uat-admin', now=START+timedelta(minutes=minutes))

    def scan(self, store=None, **changes):
        self.counter += 1
        return (store or self.store).scan({'barcode_id': self.code, 'request_id': str(self.counter), **changes})

    def test_barcode_is_stable_unique_and_backfilled_once(self):
        self.assertEqual(self.code, self.at().assign_barcode('1'))
        other = self.at(students=self.students+[student('2')]).assign_barcode('2')
        self.assertNotEqual(self.code, other)
        count = self.conn.execute("SELECT count(*) FROM attendance_audit WHERE action='assign_barcode'").fetchone()[0]
        self.assertEqual(count, 2)

    def test_checkin_and_checkout_calculate_actual_duration(self):
        self.assertEqual(self.scan()['record']['session_type'], 'regular')
        result = self.scan(self.at(90))
        self.assertEqual(result['action'], 'CHECK_OUT')
        self.assertEqual(result['record']['duration_minutes'], 90)

    def test_retry_replays_original_result_after_checkout(self):
        first = self.scan(request_id='same')
        self.scan(self.at(60))
        self.assertEqual(self.scan(self.at(65), request_id='same'), first)
        self.assertEqual(len(self.store.rows()), 1)

    def test_reusing_request_id_for_different_payload_is_rejected(self):
        self.scan(request_id='same')
        with self.assertRaisesRegex(ValueError, 'another scan'):
            self.scan(request_id='same', extra_visit=True)

    def test_double_scan_does_not_checkout(self):
        self.scan()
        self.assertEqual(self.scan()['action'], 'DUPLICATE_IGNORED')
        self.assertEqual(self.store.rows()[0]['status'], 'in_center')

    def test_client_cannot_backdate_a_scan(self):
        result = self.scan(timestamp='2000-01-01T00:00:00Z')
        self.assertEqual(result['record']['check_in'], START.isoformat())

    def test_unknown_inactive_and_future_enrolment_are_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(barcode_id='UNKNOWN')
        for changes in ({'status':'D'}, {'enrol_date':'2027-01-01'}, {'deleted_at':'2026-10-29'}):
            s = {**student(), **changes}
            with self.assertRaises(ValueError):
                self.scan(self.at(students=[s]))

    def test_branch_cannot_scan_or_correct_other_branch_records(self):
        first = self.scan()['record']
        other = self.at(scope='org:other', students=[])
        with self.assertRaises(ValueError):
            self.scan(other)
        with self.assertRaises(ValueError):
            other.correct({'record_id':first['id'], 'status':'cancelled','reason':'Cross-branch attempt'})
        self.assertEqual(other.snapshot()['records'], [])

    def test_oldest_absence_reserved_then_fulfilled_at_checkout(self):
        self.store.snapshot()
        self.at(120).snapshot()
        tomorrow = self.at(24*60)
        result = self.scan(tomorrow)
        self.assertEqual(result['record']['session_type'], 'makeup')
        self.assertEqual(tomorrow.snapshot()['absences'][0]['makeup_state'], 'reserved')
        self.scan(self.at(24*60+60))
        self.assertEqual(self.at(24*60+61).snapshot()['absences'], [])

    def test_unscheduled_without_absence_is_alert(self):
        self.students[0]['schedules'] = []
        self.assertEqual(self.scan()['record']['session_type'], 'unscheduled')

    def test_excused_absence_does_not_create_makeup(self):
        record = self.store.snapshot()['records'][0]
        self.store.correct({'record_id':record['id'], 'status':'excused', 'reason':'Parent notified illness'})
        self.assertEqual(self.scan(self.at(24*60))['record']['session_type'], 'unscheduled')

    def test_extra_visit_needs_confirmation_and_reason(self):
        self.scan(); self.scan(self.at(60))
        self.assertEqual(self.scan(self.at(90))['action'], 'REVIEW_REQUIRED')
        with self.assertRaises(ValueError):
            self.scan(self.at(90), extra_visit=True)
        result = self.scan(self.at(90), extra_visit=True, reason='Additional lesson approved')
        self.assertEqual(result['action'], 'CHECK_IN')
        self.assertEqual(len(self.store.rows()), 2)

    def test_forgotten_checkout_preserves_unknown_departure(self):
        self.scan()
        result = self.at(241).snapshot()['records'][0]
        self.assertEqual(result['status'], 'incomplete')
        self.assertIsNone(result['check_out'])
        self.assertIsNone(result['duration_minutes'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.scan(self.at(242))

    def test_holiday_excludes_expected_lessons(self):
        self.store.settings({**self.store.config,'holidays':['2026-10-29']})
        self.assertEqual(self.at().snapshot()['records'], [])

    def test_holiday_cancels_existing_absence(self):
        record = self.store.snapshot()['records'][0]
        self.at(120).snapshot()
        self.at(120).settings({**self.store.config,'holidays':['2026-10-29']})
        self.assertEqual(self.at(120).snapshot()['records'][0]['status'], 'cancelled')

    def test_schedule_snapshot_does_not_change_after_schedule_edit(self):
        self.store.snapshot()
        self.students[0]['schedules'][0]['start_time'] = '15:00'
        rows = self.at(10).snapshot()['records']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['planned_start'], START.isoformat())

    def test_new_student_after_daily_snapshot_gets_expected_lesson(self):
        self.store.snapshot()
        self.students.append(student('2', 'New UAT Student'))
        self.assertEqual(len(self.at(10).snapshot()['records']), 2)

    def test_absence_created_only_after_grace(self):
        self.assertEqual(self.at(74).snapshot()['records'][0]['status'], 'expected')
        self.assertEqual(self.at(76).snapshot()['records'][0]['status'], 'absent')

    def test_correction_uses_centre_time_and_requires_reason(self):
        row = self.scan()['record']
        store = self.at(120)
        with self.assertRaises(ValueError):
            store.correct({'record_id':row['id'],'status':'cancelled'})
        result = store.correct({'record_id':row['id'], 'status':'completed','check_in':'2026-10-29T16:00',
                                'check_out':'2026-10-29T17:20','reason':'Scanner checkout missed'})
        self.assertEqual(result['record']['duration_minutes'], 80)
        self.assertEqual(result['record']['check_out'], '2026-10-29T23:20:00+00:00')
        with self.assertRaises(ValueError):
            store.correct({'record_id':row['id'], 'status':'completed','check_in':'2026-10-29T16:00',
                           'check_out':'2026-10-30T17:20','reason':'Invalid future checkout'})

    def test_lost_tag_replacement_invalidates_old_code(self):
        result = self.store.replace_barcode({'student_id':'1','reason':'Pouch lost'})
        with self.assertRaises(ValueError):
            self.scan()
        self.assertEqual(self.scan(barcode_id=result['barcode'])['action'], 'CHECK_IN')

    def test_timezone_date_uses_centre_not_utc_date(self):
        store = self.at(150) # UTC Oct 30; local Oct 29
        self.assertEqual(store.snapshot()['today'], '2026-10-29')

    def test_nonexistent_and_ambiguous_dst_corrections_rejected(self):
        for value in ['2026-03-08T02:30', '2026-11-01T01:30']:
            with self.assertRaises(ValueError):
                parse_centre_time(value, ZoneInfo('America/Edmonton'))

    def test_future_history_does_not_create_lessons(self):
        result = self.store.snapshot('2027-01-01')
        self.assertEqual(result['records'], [])

    def test_cancelled_makeup_releases_absence(self):
        self.store.snapshot(); self.at(120).snapshot()
        row = self.scan(self.at(24*60))['record']
        store = self.at(24*60+30)
        store.correct({'record_id':row['id'],'status':'cancelled','reason':'Incorrect scan'})
        self.assertEqual(store.snapshot()['absences'][0]['makeup_state'], 'eligible')

    def test_unscheduled_visit_cannot_become_makeup_credit(self):
        self.students[0]['schedules'] = []
        row = self.scan()['record']
        with self.assertRaisesRegex(ValueError, 'Only scheduled lessons'):
            self.store.correct({'record_id':row['id'],'status':'absent','reason':'Invalid credit'})

    def test_fulfilled_absence_cannot_be_excused_without_makeup_review(self):
        self.store.snapshot(); self.at(120).snapshot()
        row = self.scan(self.at(24*60))['record']
        self.scan(self.at(24*60+60))
        with self.assertRaisesRegex(ValueError, 'linked makeup'):
            self.at(24*60+61).correct({'record_id':row['absence_id'],'status':'excused','reason':'Changed policy'})

    def test_scan_after_centre_midnight_flags_previous_visit(self):
        self.scan()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.scan(self.at(10*60))
        self.assertEqual(self.at(10*60).rows()[0]['status'], 'incomplete')

    def test_table_constraints_prevent_multiple_open_visits(self):
        row = self.scan()['record']
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.q('''INSERT INTO attendance_records
                (id,scope,student_id,lesson_key,day,session_type,status,updated_at)
                VALUES ('duplicate-open',?,?,? ,?,'unscheduled','in_center',?)''',
                (self.store.scope,'1','duplicate',row['day'],row['updated_at']))


class AttendancePermissionTests(unittest.TestCase):
    def handler(self, role):
        import app
        handler = app.Handler.__new__(app.Handler)
        handler.auth_access = {'role':role, 'email':'uat@example.test'}
        handler.auth_user = None
        return handler

    def test_operational_staff_cannot_correct_or_replace_tags(self):
        from unittest.mock import patch
        from urllib.parse import urlparse
        for role in ('Staff','Office Assistant'):
            for route in ('correct','replace-barcode','settings'):
                handler = self.handler(role)
                with patch.object(handler,'require_auth',return_value=True), patch.object(handler,'send_json') as send:
                    handler.handle_attendance(urlparse('/api/attendance/'+route), True)
                    self.assertEqual(send.call_args.args[1], 403)

    def test_office_manager_cannot_change_settings(self):
        from unittest.mock import patch
        from urllib.parse import urlparse
        handler = self.handler('Office Manager')
        with patch.object(handler,'require_auth',return_value=True), patch.object(handler,'send_json') as send:
            handler.handle_attendance(urlparse('/api/attendance/settings'), True)
            self.assertEqual(send.call_args.args[1], 403)

    def test_unauthenticated_request_never_opens_database(self):
        from unittest.mock import patch
        from urllib.parse import urlparse
        import app
        handler = self.handler('Admin')
        with patch.object(handler,'require_auth',return_value=False), patch.object(app,'db') as database:
            handler.handle_attendance(urlparse('/api/attendance'), False)
            database.assert_not_called()


if __name__ == '__main__':
    unittest.main()
