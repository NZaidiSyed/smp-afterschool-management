"""Branch-scoped attendance domain; UTC storage and centre-local lesson dates."""
import json
import secrets
import uuid
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def utcnow():
    return datetime.now(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat()


def parse_stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A timezone is required for attendance timestamps')
    return result.astimezone(UTC)


def parse_centre_time(value, zone):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    local = parsed.replace(tzinfo=zone)
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != parsed:
        raise ValueError('This local time does not exist because of daylight saving time')
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError('This time occurs twice during daylight saving; supply a timestamp with its UTC offset')
    return local.astimezone(UTC)


def ensure_schema(conn, pg=False):
    statements = [
        '''CREATE TABLE IF NOT EXISTS attendance_config (
            scope TEXT PRIMARY KEY, timezone TEXT NOT NULL, started_on TEXT NOT NULL,
            materialized_on TEXT NOT NULL, holidays TEXT NOT NULL DEFAULT '[]',
            debounce_seconds INTEGER NOT NULL DEFAULT 10, max_hours INTEGER NOT NULL DEFAULT 4,
            grace_minutes INTEGER NOT NULL DEFAULT 15)''',
        '''CREATE TABLE IF NOT EXISTS attendance_barcodes (
            scope TEXT NOT NULL, student_id TEXT NOT NULL, code TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL, PRIMARY KEY(scope,student_id))''',
        '''CREATE TABLE IF NOT EXISTS attendance_days (
            scope TEXT NOT NULL, student_id TEXT NOT NULL, day TEXT NOT NULL,
            PRIMARY KEY(scope,student_id,day))''',
        '''CREATE TABLE IF NOT EXISTS attendance_records (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, student_id TEXT NOT NULL,
            lesson_key TEXT NOT NULL, day TEXT NOT NULL, planned_start TEXT, planned_end TEXT,
            check_in TEXT, check_out TEXT, duration_minutes INTEGER,
            session_type TEXT NOT NULL, status TEXT NOT NULL,
            absence_id TEXT REFERENCES attendance_records(id), note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL, UNIQUE(scope,student_id,lesson_key))''',
        "CREATE UNIQUE INDEX IF NOT EXISTS attendance_one_open ON attendance_records(scope,student_id) WHERE status='in_center'",
        "CREATE UNIQUE INDEX IF NOT EXISTS attendance_one_makeup ON attendance_records(absence_id) WHERE absence_id IS NOT NULL AND status IN ('in_center','completed','incomplete')",
        'CREATE INDEX IF NOT EXISTS attendance_daily ON attendance_records(scope,day)',
        'CREATE INDEX IF NOT EXISTS attendance_status ON attendance_records(scope,status)',
        'CREATE INDEX IF NOT EXISTS attendance_student ON attendance_records(scope,student_id,day)',
        '''CREATE TABLE IF NOT EXISTS attendance_scan_requests (
            scope TEXT NOT NULL, request_id TEXT NOT NULL, payload TEXT NOT NULL,
            result TEXT NOT NULL, PRIMARY KEY(scope,request_id))''',
        '''CREATE TABLE IF NOT EXISTS attendance_audit (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, record_id TEXT,
            action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL,
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL)''',
    ]
    for sql in statements:
        conn.execute(sql)
    if pg:
        # Attendance is accessed through the authenticated Python API, never
        # directly through Supabase's browser-facing public schema.
        tables = ('attendance_config', 'attendance_barcodes', 'attendance_days',
                  'attendance_records', 'attendance_scan_requests', 'attendance_audit')
        roles = {row['rolname'] for row in conn.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')").fetchall()}
        for table in tables:
            conn.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            conn.execute(f'REVOKE ALL ON TABLE {table} FROM PUBLIC')
            for role in sorted(roles):
                conn.execute(f'REVOKE ALL ON TABLE {table} FROM {role}')


class Attendance:
    def __init__(self, conn, scope, students, pg=False, actor='system', now=None):
        self.conn, self.scope, self.students, self.pg = conn, scope, students, pg
        self.actor, self.now = actor, now or utcnow()
        self.by_id = {str(s['id']): s for s in students}
        self.q('INSERT INTO attendance_config(scope,timezone,started_on,materialized_on) VALUES (?,?,?,?) ON CONFLICT(scope) DO NOTHING',
               (scope, 'America/Edmonton', self.now.astimezone(ZoneInfo('America/Edmonton')).date().isoformat(), ''))
        # Serializes all attendance changes in a branch, including scan retries and reconciliation.
        suffix = ' FOR UPDATE' if pg else ''
        self.config = dict(self.q('SELECT * FROM attendance_config WHERE scope=?' + suffix, (scope,)).fetchone())
        self.zone = ZoneInfo(self.config['timezone'])
        self.today = self.now.astimezone(self.zone).date()
        self.backfill_barcodes()

    def q(self, sql, args=()):
        return self.conn.execute(sql.replace('?', '%s') if self.pg else sql, args)

    def audit(self, action, record_id='', reason='', before=None, after=None):
        self.q('INSERT INTO attendance_audit VALUES (?,?,?,?,?,?,?,?,?)',
               (str(uuid.uuid4()), self.scope, record_id, action, self.actor, reason,
                json.dumps(before or {}, default=str), json.dumps(after or {}, default=str), stamp(self.now)))

    def backfill_barcodes(self):
        assigned = {r['student_id'] for r in self.q('SELECT student_id FROM attendance_barcodes WHERE scope=?', (self.scope,)).fetchall()}
        for student in self.students:
            if str(student['id']) not in assigned:
                self.assign_barcode(student['id'])

    def assign_barcode(self, student_id):
        student_id = str(student_id)
        row = self.q('SELECT code FROM attendance_barcodes WHERE scope=? AND student_id=?', (self.scope, student_id)).fetchone()
        if row:
            return row['code']
        code = 'SMP-' + secrets.token_hex(10).upper()
        self.q('INSERT INTO attendance_barcodes VALUES (?,?,?,?) ON CONFLICT(scope,student_id) DO NOTHING',
               (self.scope, student_id, code, stamp(self.now)))
        self.audit('assign_barcode', reason='One-time student barcode assignment', after={'student_id': student_id})
        return self.q('SELECT code FROM attendance_barcodes WHERE scope=? AND student_id=?', (self.scope, student_id)).fetchone()['code']

    def active(self, student, day):
        return (not student.get('deleted_at') and str(student.get('status', '')).upper() == 'C'
                and (not student.get('enrol_date') or str(student['enrol_date'])[:10] <= day.isoformat()))

    def reconcile(self):
        # Snapshot each lesson once. Subsequent schedule edits do not rewrite historical lessons.
        last = self.config['materialized_on']
        start = date.fromisoformat(last) if last else date.fromisoformat(self.config['started_on'])
        start = max(start, date.fromisoformat(self.config['started_on']))
        # Bound work after long downtime; historical snapshots still reconcile below.
        if (self.today - start).days > 366:
            raise ValueError('Attendance has been offline for over a year; administrator review is required')
        holidays = set(json.loads(self.config['holidays']))
        day = start
        while day <= self.today:
            if day.isoformat() not in holidays:
                captured = {r['student_id'] for r in self.q('SELECT student_id FROM attendance_days WHERE scope=? AND day=?', (self.scope, day.isoformat())).fetchall()}
                for student in self.students:
                    if not self.active(student, day):
                        continue
                    student_id = str(student['id'])
                    if student_id in captured:
                        continue
                    self.q('INSERT INTO attendance_days VALUES (?,?,?)', (self.scope, student_id, day.isoformat()))
                    for schedule in student.get('schedules', []):
                        if schedule['weekday'] != WEEKDAYS[day.weekday()]:
                            continue
                        begin = datetime.combine(day, time.fromisoformat(str(schedule['start_time'])), self.zone)
                        end = datetime.combine(day, time.fromisoformat(str(schedule['end_time'])), self.zone)
                        if end <= begin:
                            continue
                        key = f'{day.isoformat()}:{schedule["start_time"]}:{schedule["end_time"]}'
                        self.q('''INSERT INTO attendance_records
                            (id,scope,student_id,lesson_key,day,planned_start,planned_end,session_type,status,updated_at)
                            VALUES (?,?,?,?,?,?,?,'regular','expected',?)
                            ON CONFLICT(scope,student_id,lesson_key) DO NOTHING''',
                            (str(uuid.uuid4()), self.scope, str(student['id']), key, day.isoformat(), stamp(begin), stamp(end), stamp(self.now)))
            day += timedelta(days=1)
        self.q('UPDATE attendance_config SET materialized_on=? WHERE scope=?', (self.today.isoformat(), self.scope))
        rows = self.rows("status IN ('expected','in_center')")
        for row in rows:
            student = self.by_id.get(row['student_id'])
            if row['status'] == 'expected' and (not student or not self.active(student, date.fromisoformat(row['day']))):
                self.update(row, {'status': 'cancelled', 'note': 'Student is no longer active'}, 'reconcile', 'Student is no longer active')
            elif row['status'] == 'expected' and row['planned_end'] and self.now > parse_stamp(row['planned_end']) + timedelta(minutes=self.config['grace_minutes']):
                status, note = ('cancelled', 'Centre closed') if row['day'] in holidays else ('absent', 'No check-in recorded')
                self.update(row, {'status': status, 'note': note}, 'reconcile', note)
            elif row['status'] == 'in_center' and (self.now - parse_stamp(row['check_in']) >= timedelta(hours=self.config['max_hours']) or row['day'] < self.today.isoformat()):
                self.update(row, {'status': 'incomplete', 'note': 'No checkout scanned; departure time needs review'}, 'flag_incomplete', 'No checkout scanned')

    def rows(self, where='', args=()):
        clause = ' AND (' + where + ')' if where else ''
        return [dict(r) for r in self.q('SELECT * FROM attendance_records WHERE scope=?' + clause + ' ORDER BY day,planned_start,check_in,id', (self.scope, *args)).fetchall()]

    def update(self, row, changes, action, reason):
        allowed = {'status', 'note', 'check_in', 'check_out', 'duration_minutes', 'session_type', 'absence_id'}
        assert set(changes) <= allowed
        changes = {**changes, 'updated_at': stamp(self.now)}
        self.q('UPDATE attendance_records SET ' + ','.join(f'{k}=?' for k in changes) + ' WHERE scope=? AND id=?',
               (*changes.values(), self.scope, row['id']))
        result = {**row, **changes}
        self.audit(action, row['id'], reason, row, result)
        return result

    def snapshot(self, day=None):
        self.reconcile()
        day = date.fromisoformat(day or self.today.isoformat()).isoformat()
        records = self.rows("day=? OR status='in_center' OR (status='absent' AND NOT EXISTS (SELECT 1 FROM attendance_records AS makeup WHERE makeup.scope=attendance_records.scope AND makeup.absence_id=attendance_records.id AND makeup.status='completed'))", (day,))
        links = self.q("SELECT absence_id,status FROM attendance_records WHERE scope=? AND absence_id IS NOT NULL AND status IN ('in_center','incomplete','completed')", (self.scope,)).fetchall()
        fulfilled = {r['absence_id'] for r in links if r['status'] == 'completed'}
        reserved = {r['absence_id'] for r in links if r['status'] in ('in_center', 'incomplete')}
        codes = {r['student_id']: r['code'] for r in self.q('SELECT * FROM attendance_barcodes WHERE scope=?', (self.scope,)).fetchall()}
        def enrich(row):
            s = self.by_id.get(row['student_id'], {})
            return {**row, 'student_name': s.get('student_name', 'Archived student'),
                    'scheduled_days': ', '.join(dict.fromkeys(x['weekday'] for x in s.get('schedules', []))),
                    'makeup_state': 'fulfilled' if row['id'] in fulfilled else 'reserved' if row['id'] in reserved else 'eligible' if row['status'] == 'absent' else ''}
        return {'ok': True, 'today': self.today.isoformat(), 'date': day, 'timezone': self.config['timezone'],
                'settings': self.config, 'records': [enrich(r) for r in records if r['day'] == day],
                'open_sessions': [enrich(r) for r in records if r['status'] == 'in_center'],
                'absences': [enrich(r) for r in records if r['status'] == 'absent' and r['session_type'] == 'regular' and r['id'] not in fulfilled],
                'students': [{'id': str(s['id']), 'name': s['student_name'], 'status': s.get('status'),
                              'barcode': codes.get(str(s['id']), ''), 'schedule': s.get('weekly_schedule', '')} for s in self.students],
                'audit': [dict(r) for r in self.q('SELECT * FROM attendance_audit WHERE scope=? ORDER BY created_at DESC LIMIT 100', (self.scope,)).fetchall()]}

    def scan(self, payload):
        request_id = str(payload.get('request_id', ''))
        code = str(payload.get('barcode_id', '')).strip().upper()
        if not request_id or len(request_id) > 100 or not code or len(code) > 64:
            raise ValueError('Barcode and unique request ID are required')
        fingerprint = json.dumps({k: payload.get(k) for k in ['barcode_id', 'extra_visit', 'reason']}, sort_keys=True)
        previous = self.q('SELECT * FROM attendance_scan_requests WHERE scope=? AND request_id=?', (self.scope, request_id)).fetchone()
        if previous:
            if previous['payload'] != fingerprint:
                raise ValueError('Request ID was already used for another scan')
            return json.loads(previous['result'])
        tag = self.q('SELECT * FROM attendance_barcodes WHERE scope=? AND code=?', (self.scope, code)).fetchone()
        student = self.by_id.get(tag['student_id']) if tag else None
        if not student or not self.active(student, self.today):
            raise ValueError('Unknown, inactive, or unavailable student barcode')
        self.reconcile()
        student_id = str(student['id'])
        records = self.rows('student_id=?', (student_id,))
        recent = [r for r in records if r['check_in']]
        recent.sort(key=lambda r: r['check_out'] or r['check_in'])
        if recent and (self.now - parse_stamp(recent[-1]['check_out'] or recent[-1]['check_in'])).total_seconds() < self.config['debounce_seconds']:
            result = {'ok': True, 'action': 'DUPLICATE_IGNORED', 'message': 'Repeated scan ignored. Wait before scanning again.', 'student_name': student['student_name']}
        else:
            opened = next((r for r in records if r['status'] == 'in_center'), None)
            if opened:
                duration = int((self.now - parse_stamp(opened['check_in'])).total_seconds() // 60)
                record = self.update(opened, {'check_out': stamp(self.now), 'duration_minutes': duration, 'status': 'completed'}, 'check_out', 'Barcode checkout')
                result = {'ok': True, 'action': 'CHECK_OUT', 'message': f'Checked out · {duration} minutes', 'record': record, 'student_name': student['student_name']}
            else:
                if any(r['status'] == 'incomplete' for r in records):
                    raise ValueError('An incomplete session requires staff correction before another visit')
                today = [r for r in records if r['day'] == self.today.isoformat()]
                candidates = [r for r in today if r['session_type'] == 'regular' and r['status'] in ('expected', 'absent')]
                candidates.sort(key=lambda r: abs((parse_stamp(r['planned_start']) - self.now).total_seconds()))
                if candidates:
                    record = self.update(candidates[0], {'check_in': stamp(self.now), 'status': 'in_center', 'note': ''}, 'check_in', 'Scheduled lesson')
                else:
                    completed = any(r['check_in'] for r in today)
                    if completed and not payload.get('extra_visit'):
                        return {'ok': True, 'action': 'REVIEW_REQUIRED', 'message': 'Session already completed today. Staff must confirm an extra visit.', 'student_name': student['student_name']}
                    if completed and not str(payload.get('reason', '')).strip():
                        raise ValueError('A reason is required for an extra visit')
                    claimed = {r['absence_id'] for r in records if r['absence_id'] and r['status'] in ('in_center','completed','incomplete')}
                    scheduled_today = any(r['planned_start'] for r in today)
                    absence = next((r for r in records if r['session_type'] == 'regular' and r['status'] == 'absent' and r['day'] < self.today.isoformat() and r['id'] not in claimed), None) if not scheduled_today else None
                    kind = 'makeup' if absence else 'unscheduled'
                    record = {'id': str(uuid.uuid4()), 'scope': self.scope, 'student_id': student_id,
                              'lesson_key': str(uuid.uuid4()), 'day': self.today.isoformat(), 'check_in': stamp(self.now),
                              'session_type': kind, 'status': 'in_center', 'absence_id': absence['id'] if absence else None,
                              'note': str(payload.get('reason', '')).strip()[:1000], 'updated_at': stamp(self.now)}
                    self.q('INSERT INTO attendance_records (' + ','.join(record) + ') VALUES (' + ','.join('?' for _ in record) + ')', tuple(record.values()))
                    self.audit('check_in', record['id'], record['note'], after=record)
                result = {'ok': True, 'action': 'CHECK_IN', 'record': record, 'student_name': student['student_name'],
                          'message': 'Makeup reserved; fulfilment follows completed checkout' if record['session_type'] == 'makeup' else 'Unscheduled visit · staff review required' if record['session_type'] == 'unscheduled' else 'Scheduled lesson check-in'}
        self.q('INSERT INTO attendance_scan_requests VALUES (?,?,?,?)', (self.scope, request_id, fingerprint, json.dumps(result)))
        return result

    def correct(self, payload):
        reason = str(payload.get('reason', '')).strip()
        if not reason or len(reason) > 1000:
            raise ValueError('A correction reason is required (maximum 1000 characters)')
        row = next(iter(self.rows('id=?', (payload.get('record_id'),))), None)
        if not row:
            raise ValueError('Attendance record not found in this branch')
        status = payload.get('status')
        if status not in ('completed','incomplete','excused','cancelled','absent'):
            raise ValueError('Choose a valid correction status')
        if status in ('absent', 'excused') and (row['session_type'] != 'regular' or not row['planned_start']):
            raise ValueError('Only scheduled lessons can be marked absent or excused')
        changes = {'status': status, 'note': reason}
        if status == 'completed':
            start = parse_centre_time(payload.get('check_in') or row['check_in'] or '', self.zone)
            end = parse_centre_time(payload.get('check_out') or '', self.zone)
            if end <= start or end > self.now or start.astimezone(self.zone).date().isoformat() != row['day']:
                raise ValueError('Use valid arrival/departure times on the session date; departure cannot be in the future')
            changes.update(check_in=stamp(start), check_out=stamp(end), duration_minutes=int((end-start).total_seconds()//60))
        elif status in ('excused','cancelled','absent'):
            if self.rows("absence_id=? AND status IN ('in_center','completed','incomplete')", (row['id'],)):
                raise ValueError('Correct or cancel the linked makeup session first')
            changes.update(check_in=None, check_out=None, duration_minutes=None, absence_id=None)
        else:
            changes.update(check_out=None, duration_minutes=None)
        record = self.update(row, changes, 'correction', reason)
        return {'ok': True, 'record': record}

    def settings(self, payload):
        zone = str(payload.get('timezone', ''))
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Choose a valid IANA timezone, e.g. America/Edmonton')
        if zone != self.config['timezone'] and self.rows():
            raise ValueError('Timezone cannot change after lessons exist; historical records need a migration')
        holidays = [date.fromisoformat(str(d)).isoformat() for d in payload.get('holidays', [])]
        debounce, hours, grace = int(payload.get('debounce_seconds', 10)), int(payload.get('max_hours', 4)), int(payload.get('grace_minutes', 15))
        if not 2 <= debounce <= 120 or not 1 <= hours <= 12 or not 0 <= grace <= 120:
            raise ValueError('Duplicate window: 2–120 seconds; session limit: 1–12 hours; grace: 0–120 minutes')
        self.q('UPDATE attendance_config SET timezone=?,holidays=?,debounce_seconds=?,max_hours=?,grace_minutes=? WHERE scope=?',
               (zone,json.dumps(sorted(set(holidays))),debounce,hours,grace,self.scope))
        if zone != self.config['timezone']:
            self.q('UPDATE attendance_config SET started_on=?,materialized_on=? WHERE scope=?',
                   (self.now.astimezone(ZoneInfo(zone)).date().isoformat(), '', self.scope))
            self.q('DELETE FROM attendance_days WHERE scope=?', (self.scope,))
        for row in self.rows("status IN ('expected','absent')"):
            if row['day'] in holidays and row['status'] in ('expected','absent'):
                self.update(row, {'status': 'cancelled', 'note': 'Centre closed'}, 'holiday', 'Centre closure')
        self.audit('settings', reason='Attendance settings updated', before=self.config, after=payload)
        return {'ok': True}

    def replace_barcode(self, payload):
        student_id = str(payload.get('student_id', ''))
        reason = str(payload.get('reason', '')).strip()
        if student_id not in self.by_id or not reason:
            raise ValueError('Student and replacement reason are required')
        old = self.assign_barcode(student_id)
        code = 'SMP-' + secrets.token_hex(10).upper()
        self.q('UPDATE attendance_barcodes SET code=?,created_at=? WHERE scope=? AND student_id=?', (code,stamp(self.now),self.scope,student_id))
        self.audit('replace_barcode', reason=reason[:1000], before={'student_id': student_id,'code':old}, after={'code':code})
        return {'ok': True, 'barcode': code}
