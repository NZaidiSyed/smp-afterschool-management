"""Database-backed schedule drafts, recurring availability, swaps and in-app inbox."""
import json
import uuid
from datetime import date, datetime, timedelta, timezone


class SchedulingError(ValueError):
    status = 400


class SchedulingConflict(SchedulingError):
    status = 409


class SchedulingDenied(SchedulingError):
    status = 403


def monday(value):
    day = date.fromisoformat(str(value))
    if day.weekday() != 0:
        raise SchedulingError('Choose a Monday as the week start')
    return day.isoformat()


def minutes(value):
    try:
        hour, minute = map(int, str(value).split(':'))
    except (ValueError, TypeError):
        raise SchedulingError('Use a valid HH:MM time')
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise SchedulingError('Use a valid HH:MM time')
    return hour*60+minute


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def ensure_schema(conn, pg=False):
    statements = [
        'CREATE TABLE IF NOT EXISTS scheduler_locks(scope TEXT PRIMARY KEY)',
        '''CREATE TABLE IF NOT EXISTS scheduler_weeks (
            scope TEXT NOT NULL, week_start TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
            draft_json TEXT NOT NULL DEFAULT '[]', published_json TEXT NOT NULL DEFAULT '[]',
            published_revision INTEGER NOT NULL DEFAULT 0, published_at TEXT, published_by TEXT,
            PRIMARY KEY(scope,week_start))''',
        '''CREATE TABLE IF NOT EXISTS scheduler_availability (
            scope TEXT NOT NULL, staff_id TEXT NOT NULL, effective_from TEXT NOT NULL,
            revision INTEGER NOT NULL, windows_json TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(scope,staff_id,effective_from))''',
        '''CREATE TABLE IF NOT EXISTS scheduler_swaps (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, week_start TEXT NOT NULL,
            owner_id TEXT NOT NULL, target_id TEXT NOT NULL, source_json TEXT NOT NULL, target_json TEXT,
            status TEXT NOT NULL, revision INTEGER NOT NULL, reason TEXT NOT NULL,
            decision_reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        'CREATE INDEX IF NOT EXISTS scheduler_swaps_scope ON scheduler_swaps(scope,week_start,status)',
        '''CREATE TABLE IF NOT EXISTS scheduler_notifications (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, staff_id TEXT NOT NULL, event_key TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, week_start TEXT NOT NULL, schedule_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, read_at TEXT, acknowledged_at TEXT,
            UNIQUE(scope,staff_id,event_key))''',
        'CREATE INDEX IF NOT EXISTS scheduler_inbox ON scheduler_notifications(scope,staff_id,created_at)',
        '''CREATE TABLE IF NOT EXISTS scheduler_audit (
            id TEXT PRIMARY KEY, scope TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
            details_json TEXT NOT NULL, created_at TEXT NOT NULL)''',
    ]
    for sql in statements:
        conn.execute(sql)
    if pg:
        roles={r['rolname'] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated')").fetchall()}
        for table in ('scheduler_locks','scheduler_weeks','scheduler_availability','scheduler_swaps','scheduler_notifications','scheduler_audit'):
            conn.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            conn.execute(f'REVOKE ALL ON TABLE {table} FROM PUBLIC')
            for role in sorted(roles):
                conn.execute(f'REVOKE ALL ON TABLE {table} FROM {role}')


class Scheduler:
    def __init__(self, conn, scope, staff, actor, staff_id=None, manager=False, pg=False, leave=None, now=None, legacy_loader=None):
        self.conn,self.scope,self.pg=conn,scope,pg
        self.actor,self.staff_id,self.manager=actor,str(staff_id) if staff_id is not None else None,manager
        self.staff={str(s['id']):s for s in staff if s.get('active',True)}
        self.legacy_loader=legacy_loader
        self.leave=leave or []
        self.now=(now or datetime.now(timezone.utc)).isoformat()
        self.q('INSERT INTO scheduler_locks VALUES (?) ON CONFLICT(scope) DO NOTHING',(scope,))
        self.q('SELECT scope FROM scheduler_locks WHERE scope=?'+(' FOR UPDATE' if pg else ''),(scope,)).fetchone()

    def q(self, sql, args=()):
        return self.conn.execute(sql.replace('?','%s') if self.pg else sql,args)

    def require_manager(self):
        if not self.manager:
            raise SchedulingDenied('Only a manager can change or publish the team schedule')

    def audit(self, action, details):
        self.q('INSERT INTO scheduler_audit VALUES (?,?,?,?,?,?)',(str(uuid.uuid4()),self.scope,action,self.actor,compact(details),self.now))

    def week(self, value):
        value=monday(value)
        self.q('INSERT INTO scheduler_weeks(scope,week_start) VALUES (?,?) ON CONFLICT(scope,week_start) DO NOTHING',(self.scope,value))
        row=dict(self.q('SELECT * FROM scheduler_weeks WHERE scope=? AND week_start=?',(self.scope,value)).fetchone())
        if not row['revision'] and self.legacy_loader:
            imported=self.legacy_loader(value)
            if imported:
                shifts=self.normalise(value, imported)
                self.q('UPDATE scheduler_weeks SET draft_json=?,revision=1 WHERE scope=? AND week_start=?',(compact(shifts),self.scope,value))
                self.audit('import_legacy_draft',{'week':value,'shifts':len(shifts),'original_rows_preserved':True})
                row['draft_json']=compact(shifts);row['revision']=1
        return row

    def match_revision(self, row, revision):
        if revision is None or int(revision)!=row['revision']:
            raise SchedulingConflict('This schedule changed on another device. Refresh before saving.')

    def normalise(self, week, shifts):
        if not isinstance(shifts,list) or len(shifts)>300:
            raise SchedulingError('Provide at most 300 shifts per week')
        start=date.fromisoformat(monday(week));seen=set();clean=[]
        for shift in shifts:
            staff_id=str(shift.get('staff_id',''))
            if staff_id not in self.staff:
                raise SchedulingError('A shift refers to inactive staff or another branch')
            day=date.fromisoformat(str(shift.get('day','')))
            if not start<=day<start+timedelta(days=7):
                raise SchedulingError('Every shift must belong to the selected week')
            begin,end=minutes(shift.get('start')),minutes(shift.get('end'))
            rest=int(shift.get('break_minutes',0))
            if end<=begin or not 0<=rest<end-begin:
                raise SchedulingError('End must follow start and breaks must be shorter than the shift')
            shift_id=str(shift.get('id') or uuid.uuid4())
            if len(shift_id)>100 or shift_id in seen:
                raise SchedulingError('Shift IDs must be unique within the week')
            seen.add(shift_id)
            clean.append({'id':shift_id,'staff_id':staff_id,'day':day.isoformat(),
                          'start':f'{begin//60:02}:{begin%60:02}','end':f'{end//60:02}:{end%60:02}',
                          'break_minutes':rest,'kind':str(shift.get('kind') or 'Teaching')[:60],
                          'location':str(shift.get('location') or '')[:120],'notes':str(shift.get('notes') or '')[:1000]})
        return sorted(clean,key=lambda s:(s['day'],s['start'],s['staff_id'],s['id']))

    def availability_rows(self):
        return [dict(r) for r in self.q('SELECT * FROM scheduler_availability WHERE scope=? ORDER BY effective_from',(self.scope,)).fetchall()]

    def checks(self, shifts):
        errors=[];warnings=[];availability=self.availability_rows()
        for shift in shifts:
            sid=shift['staff_id'];name=self.staff.get(sid,{}).get('name','Inactive staff')
            if sid not in self.staff:
                errors.append(f'{name}: inactive or unavailable staff member')
                continue
            begin,end=minutes(shift['start']),minutes(shift['end'])
            for other in shifts:
                if shift['id']<other['id'] and sid==other['staff_id'] and shift['day']==other['day'] and begin<minutes(other['end']) and end>minutes(other['start']):
                    errors.append(f'{name}: overlapping shifts on {shift["day"]}')
            records=[a for a in availability if a['staff_id']==sid and a['effective_from']<=shift['day']]
            windows=json.loads(records[-1]['windows_json']) if records else {}
            key=str(date.fromisoformat(shift['day']).weekday())
            if key not in windows or windows[key] is None:
                warnings.append(f'{name}: availability not provided for {shift["day"]}')
            elif not any(minutes(w['start'])<=begin and end<=minutes(w['end']) for w in windows[key]):
                errors.append(f'{name}: outside available hours on {shift["day"]}')
            for item in self.leave:
                if str(item['staff_id'])==sid and item['start_date']<=shift['day']<=item['end_date']:
                    errors.append(f'{name}: approved time off on {shift["day"]}')
        return {'conflicts':sorted(set(errors)),'warnings':sorted(set(warnings))}

    def save(self, payload):
        self.require_manager();row=self.week(payload.get('week_start'));self.match_revision(row,payload.get('revision'))
        shifts=self.normalise(row['week_start'],payload.get('shifts'))
        self.q('UPDATE scheduler_weeks SET draft_json=?,revision=revision+1 WHERE scope=? AND week_start=?',(compact(shifts),self.scope,row['week_start']))
        self.audit('save_draft',{'week':row['week_start'],'revision':row['revision']+1,'before':json.loads(row['draft_json']),'after':shifts})
        return {'ok':True,'revision':row['revision']+1,**self.checks(shifts)}

    def copy(self, payload, preview=False):
        self.require_manager();source=self.week(payload.get('source_week'));target=self.week(payload.get('week_start'))
        if source['week_start']==target['week_start']:
            raise SchedulingError('Source and target weeks must differ')
        if payload.get('mode') not in ('merge','replace') or payload.get('source_version') not in ('published','draft'):
            raise SchedulingError('Choose merge/replace and published/draft source')
        self.match_revision(target,payload.get('revision'))
        version='published_revision' if payload['source_version']=='published' else 'revision'
        if not preview and int(payload.get('source_revision',-1))!=source[version]:
            raise SchedulingConflict('The source week changed after preview. Preview again.')
        items=json.loads(source['published_json'] if payload['source_version']=='published' else source['draft_json'])
        if not items:
            raise SchedulingError('The selected source schedule has no shifts')
        offset=date.fromisoformat(target['week_start'])-date.fromisoformat(source['week_start'])
        copied=[{**s,'id':str(uuid.uuid4()),'day':(date.fromisoformat(s['day'])+offset).isoformat()} for s in items]
        existing=json.loads(target['draft_json']) if payload['mode']=='merge' else []
        key=lambda s:tuple(s.get(k) for k in ('staff_id','day','start','end','kind','location','break_minutes'))
        keys={key(s) for s in existing}
        shifts=existing+[s for s in copied if key(s) not in keys]
        shifts=self.normalise(target['week_start'],shifts)
        if not preview:
            result=self.save({'week_start':target['week_start'],'revision':target['revision'],'shifts':shifts})
            self.audit('copy_week',{'source_week':source['week_start'],'target_week':target['week_start'],'mode':payload['mode'],'source_version':payload['source_version']})
            return result
        return {'ok':True,'source_revision':source[version],'revision':target['revision'],'shifts':shifts,**self.checks(shifts)}

    def publish(self, payload, title='Schedule published'):
        self.require_manager();row=self.week(payload.get('week_start'));self.match_revision(row,payload.get('revision'))
        shifts=json.loads(row['draft_json']);checks=self.checks(shifts)
        if not row['revision']:
            raise SchedulingError('Save a draft before publishing')
        if checks['conflicts']:
            raise SchedulingError('Resolve conflicts before publishing: '+'; '.join(checks['conflicts']))
        if checks['warnings'] and not payload.get('accept_warnings'):
            raise SchedulingError('Review and explicitly accept missing-availability warnings before publishing')
        if row['published_revision'] and row['draft_json']==row['published_json']:
            return {'ok':True,'notifications_created':0,'message':'No changes since the last publication'}
        self.q('UPDATE scheduler_weeks SET published_json=draft_json,published_revision=revision,published_at=?,published_by=? WHERE scope=? AND week_start=?',(self.now,self.actor,self.scope,row['week_start']))
        recipients={s['staff_id'] for s in shifts+json.loads(row['published_json'])}
        count=0
        for sid in sorted(recipients & self.staff.keys()):
            self.notify(sid,f'published:{row["week_start"]}:{row["revision"]}',title,
                        f'Week of {row["week_start"]}, revision {row["revision"]}. Review your shifts and acknowledge this revision.',row['week_start'],row['revision'])
            count+=1
        self.audit('publish',{'week':row['week_start'],'revision':row['revision'],'recipients':sorted(recipients),'warnings_accepted':bool(payload.get('accept_warnings'))})
        return {'ok':True,'notifications_created':count,'message':'Published; in-app notifications are available. Email/SMS were not sent.'}

    def notify(self, sid, key, title, body, week, revision=0):
        self.q('''INSERT INTO scheduler_notifications
            (id,scope,staff_id,event_key,title,body,week_start,schedule_revision,created_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,staff_id,event_key) DO NOTHING''',
            (str(uuid.uuid4()),self.scope,sid,key,title,body,week,revision,self.now))

    def notify_managers(self, key, title, body, week):
        roles={'owner','admin','administrator','principal_owner','principal','office_manager','office manager','manager'}
        for sid, person in self.staff.items():
            if str(person.get('role','')).lower() in roles:
                self.notify(sid,key,title,body,week)

    def availability(self, payload):
        sid=str(payload.get('staff_id') or self.staff_id or '')
        if sid not in self.staff or (not self.manager and sid!=self.staff_id):
            raise SchedulingDenied('You can only update your own availability')
        effective=date.fromisoformat(str(payload.get('effective_from'))).isoformat()
        windows=payload.get('windows')
        if not isinstance(windows,dict) or set(windows)-set(map(str,range(7))):
            raise SchedulingError('Availability must use weekdays 0–6')
        for values in windows.values():
            if values is None:
                continue
            if not isinstance(values,list) or len(values)>1:
                raise SchedulingError('Provide at most one availability window per day')
            last=-1
            for window in sorted(values,key=lambda w:minutes(w['start'])):
                start,end=minutes(window['start']),minutes(window['end'])
                if end<=start or start<last:
                    raise SchedulingError('Availability windows must be valid and cannot overlap')
                last=end
        old=self.q('SELECT revision FROM scheduler_availability WHERE scope=? AND staff_id=? AND effective_from=?',(self.scope,sid,effective)).fetchone()
        revision=old['revision'] if old else 0
        if int(payload.get('revision',-1))!=revision:
            raise SchedulingConflict('Availability changed on another device. Refresh before saving.')
        self.q('''INSERT INTO scheduler_availability VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(scope,staff_id,effective_from) DO UPDATE SET revision=excluded.revision,windows_json=excluded.windows_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
            (self.scope,sid,effective,revision+1,compact(windows),self.actor,self.now))
        self.audit('availability',{'staff_id':sid,'effective_from':effective,'windows':windows,'revision':revision+1})
        if not self.manager:
            day=date.fromisoformat(effective)
            self.notify_managers(f'availability:{sid}:{effective}:{revision+1}', 'Staff availability changed',
                                 f'{self.staff[sid]["name"]} updated availability effective {effective}. Review unpublished schedules.',
                                 (day-timedelta(days=day.weekday())).isoformat())
        return {'ok':True}

    def request_swap(self, payload):
        if not self.staff_id or self.staff_id not in self.staff:
            raise SchedulingDenied('Sign in as the staff member offering the shift')
        row=self.week(payload.get('week_start'));published=json.loads(row['published_json'])
        source=next((s for s in published if s['id']==payload.get('source_id') and s['staff_id']==self.staff_id),None)
        target_id=str(payload.get('target_staff_id',''))
        if not source or target_id not in self.staff or target_id==self.staff_id:
            raise SchedulingError('Choose your published shift and another active colleague')
        target=next((s for s in published if s['id']==payload.get('target_shift_id') and s['staff_id']==target_id),None)
        if payload.get('target_shift_id') and not target:
            raise SchedulingError('The colleague exchange shift is no longer available')
        reason=str(payload.get('reason','')).strip()
        if not reason:
            raise SchedulingError('A swap reason is required')
        for request in self.swap_rows():
            if request['status'] in ('awaiting_colleague','awaiting_manager') and (json.loads(request['source_json'])['id']==source['id'] or request['target_json'] and json.loads(request['target_json'])['id']==source['id']):
                raise SchedulingConflict('This shift already has a pending request')
        request_id=str(uuid.uuid4())
        self.q('INSERT INTO scheduler_swaps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (request_id,self.scope,row['week_start'],self.staff_id,target_id,compact(source),compact(target) if target else None,'awaiting_colleague',1,reason[:1000],'',self.now,self.now))
        self.notify(target_id,'swap-offer:'+request_id,'Shift cover / exchange requested',f'{self.staff[self.staff_id]["name"]} requests a shift change. Review the Swaps tab.',row['week_start'])
        self.audit('request_swap',{'id':request_id,'owner_id':self.staff_id,'target_id':target_id})
        return {'ok':True,'id':request_id}

    def swap_rows(self):
        return [dict(r) for r in self.q('SELECT * FROM scheduler_swaps WHERE scope=? ORDER BY created_at DESC',(self.scope,)).fetchall()]

    def decide_swap(self, payload):
        request=next((r for r in self.swap_rows() if r['id']==payload.get('id')),None)
        if not request:
            raise SchedulingError('Swap request not found in this branch')
        if int(payload.get('revision',-1))!=request['revision']:
            raise SchedulingConflict('This swap changed. Refresh before deciding.')
        action=payload.get('action');status=request['status'];reason=str(payload.get('reason','')).strip()
        if action=='cancel' and self.staff_id==request['owner_id'] and status in ('awaiting_colleague','awaiting_manager'):
            state='cancelled'
        elif action in ('accept','decline') and self.staff_id==request['target_id'] and status=='awaiting_colleague':
            state='awaiting_manager' if action=='accept' else 'rejected'
        elif action in ('approve','reject') and self.manager and status in ('awaiting_colleague','awaiting_manager'):
            if not reason:
                raise SchedulingError('Manager decisions require a reason')
            if action=='approve' and status!='awaiting_manager':
                raise SchedulingError('The colleague must accept before manager approval')
            state='approved' if action=='approve' else 'rejected'
        else:
            raise SchedulingDenied('This decision is not permitted for your role or the current request status')
        if state=='approved':
            row=self.week(request['week_start'])
            if row['draft_json']!=row['published_json']:
                raise SchedulingConflict('This week has unpublished edits. Publish or discard the draft before approving a swap.')
            shifts=json.loads(row['published_json']);source=json.loads(request['source_json']);target=json.loads(request['target_json']) if request['target_json'] else None
            for expected in [source]+([target] if target else []):
                if not any(s==expected for s in shifts):
                    raise SchedulingConflict('A requested shift changed after submission. Cancel this request and submit a new one.')
            for shift in shifts:
                if shift['id']==source['id']:
                    shift['staff_id']=request['target_id']
                elif target and shift['id']==target['id']:
                    shift['staff_id']=request['owner_id']
            checks=self.checks(shifts)
            if checks['conflicts']:
                raise SchedulingError('Swap conflicts: '+'; '.join(checks['conflicts']))
            saved=self.save({'week_start':row['week_start'],'revision':row['revision'],'shifts':shifts})
            self.publish({'week_start':row['week_start'],'revision':saved['revision'],'accept_warnings':True},'Schedule updated after approved swap')
        self.q('UPDATE scheduler_swaps SET status=?,revision=revision+1,decision_reason=?,updated_at=? WHERE scope=? AND id=?',(state,reason[:1000],self.now,self.scope,request['id']))
        if state=='awaiting_manager':
            self.notify_managers('swap-approval:'+request['id'], 'Shift change needs approval',
                                 'The colleague accepted a shift change. Review conflicts in Shift swaps before approving.', request['week_start'])
        for sid in (request['owner_id'],request['target_id']):
            self.notify(sid,f'swap:{request["id"]}:{request["revision"]+1}','Shift request updated',f'Request status: {state.replace("_"," ")}. '+reason[:500],request['week_start'])
        self.audit('swap_'+state,{'id':request['id'],'reason':reason})
        return {'ok':True,'status':state}

    def inbox_action(self, payload):
        row=self.q('SELECT * FROM scheduler_notifications WHERE scope=? AND id=? AND staff_id=?',(self.scope,str(payload.get('id','')),self.staff_id or '')).fetchone()
        if not row:
            raise SchedulingDenied('Only the recipient can read or acknowledge this notification')
        if payload.get('action') not in ('read','acknowledge'):
            raise SchedulingError('Choose read or acknowledge')
        if payload['action']=='acknowledge' and row['schedule_revision']:
            current=self.week(row['week_start'])
            if row['schedule_revision']!=current['published_revision']:
                raise SchedulingConflict('A newer schedule is published. Review and acknowledge the latest revision.')
        self.q('UPDATE scheduler_notifications SET read_at=COALESCE(read_at,?),acknowledged_at=CASE WHEN ? THEN COALESCE(acknowledged_at,?) ELSE acknowledged_at END WHERE scope=? AND id=?',
               (self.now,payload['action']=='acknowledge',self.now,self.scope,row['id']))
        return {'ok':True}

    def snapshot(self, week):
        row=self.week(week)
        published=json.loads(row['published_json']);draft=json.loads(row['draft_json'])
        shifts=draft if self.manager else [s for s in published if s['staff_id']==self.staff_id]
        # Staff need only basic team shift details to choose a colleague exchange.
        team=[{k:s[k] for k in ('id','staff_id','day','start','end','kind')} for s in published]
        inbox=[dict(r) for r in self.q('SELECT * FROM scheduler_notifications WHERE scope=? AND staff_id=? ORDER BY created_at DESC LIMIT 100',(self.scope,self.staff_id or '')).fetchall()]
        swaps=[r for r in self.swap_rows() if self.manager or self.staff_id in (r['owner_id'],r['target_id'])]
        availability=[r for r in self.availability_rows() if self.manager or r['staff_id']==self.staff_id]
        receipts=[dict(r) for r in self.q('SELECT staff_id,created_at,read_at,acknowledged_at FROM scheduler_notifications WHERE scope=? AND week_start=? AND schedule_revision=?',(self.scope,row['week_start'],row['published_revision'])).fetchall()] if self.manager and row['published_revision'] else []
        return {'ok':True,'actor':{'name':self.actor,'staff_id':self.staff_id,'manager':self.manager},
                'week_start':row['week_start'],'revision':row['revision'] if self.manager else None,
                'published_revision':row['published_revision'],'published_at':row['published_at'],
                'has_unpublished_changes':row['draft_json']!=row['published_json'] if self.manager else False,
                'shifts':shifts,'team_shifts':team,'staff':[{'id':sid,'name':s['name'],'role':s.get('role','staff')} for sid,s in self.staff.items()],
                'availability':availability,'swaps':swaps,'inbox':inbox,'receipts':receipts,
                'checks':self.checks(draft) if self.manager else {'conflicts':[],'warnings':[]},
                'audit':[dict(r) for r in self.q('SELECT * FROM scheduler_audit WHERE scope=? ORDER BY created_at DESC LIMIT 75',(self.scope,)).fetchall()] if self.manager else []}
