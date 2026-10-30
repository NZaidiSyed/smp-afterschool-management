"""Synthetic staff accounts and a source week, only in a dedicated UAT database."""
from pathlib import Path
import sys
from datetime import date,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from staff_scheduling import Scheduler,ensure_schema


def main():
    if app.PG_MODE or app.SUPABASE_REQUIRE_AUTH or 'uat' not in str(app.DATA_DIR).lower():
        raise SystemExit('Use a dedicated local UAT SQLite directory without production authentication.')
    with app.db() as conn:
        ensure_schema(conn)
        branch=app.current_branch_id(conn)
        accounts=[('Scheduling UAT Manager','manager@schedule.uat','office_manager'),
                  ('Scheduling UAT Alex','alex@schedule.uat','staff'),('Scheduling UAT Jamie','jamie@schedule.uat','staff'),('Scheduling UAT Taylor','taylor@schedule.uat','staff')]
        for name,email,role in accounts:
            if not conn.execute('SELECT id FROM staff_members WHERE email=?',(email,)).fetchone():
                # Test-only password, recognised by the existing local staff verifier.
                conn.execute('''INSERT INTO staff_members(branch_id,staff_name,email,role,role_title,subject,active,password_hash)
                    VALUES (?,?,?,?,?,'Teaching',1,'UatSchedule2026!')''',(branch,name,email,role,role))
        staff=[{'id':str(r['id']),'name':r['staff_name'],'active':bool(r['active'])} for r in conn.execute('SELECT * FROM staff_members WHERE branch_id=?',(branch,)).fetchall()]
        scope=f'local:{branch}';store=Scheduler(conn,scope,staff,'uat-fixture',manager=True)
        for person in staff:
            if not store.q('SELECT staff_id FROM scheduler_availability WHERE scope=? AND staff_id=?',(scope,person['id'])).fetchone():
                store.availability({'staff_id':person['id'],'effective_from':'2026-01-01','revision':0,'windows':{str(day):[{'start':'13:00','end':'21:00'}] for day in range(7)}})
        week=(date.today()-timedelta(days=date.today().weekday())).isoformat();row=store.week(week)
        if not row['revision']:
            alex=next(p for p in staff if p['name']=='Scheduling UAT Alex');jamie=next(p for p in staff if p['name']=='Scheduling UAT Jamie')
            tuesday=(date.fromisoformat(week)+timedelta(days=1)).isoformat()
            shifts=[{'staff_id':p['id'],'day':tuesday,'start':'15:00','end':'18:00','kind':'Teaching','location':'Room '+str(i+1)} for i,p in enumerate((alex,jamie))]
            store.save({'week_start':week,'revision':0,'shifts':shifts})
            store.publish({'week_start':week,'revision':1})
        conn.commit()
    print('Prepared synthetic manager, Alex, Jamie and Taylor. Existing UAT edits are preserved.')
    print('Test accounts: manager@schedule.uat / alex@schedule.uat / jamie@schedule.uat / taylor@schedule.uat')
    print('Test-only password: UatSchedule2026!')


if __name__=='__main__':
    main()
