"""Four priority scheduling workflows with role, revision and branch protection."""
import sqlite3
import unittest
from datetime import datetime,timezone
from staff_scheduling import Scheduler,SchedulingError,SchedulingConflict,SchedulingDenied,ensure_schema

WEEK='2026-11-02'
NEXT='2026-11-09'
STAFF=[{'id':'a','name':'Alex'},{'id':'b','name':'Jamie'},{'id':'c','name':'Taylor'}]


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        ensure_schema(self.conn);ensure_schema(self.conn)
        self.store=self.as_user()
        for s in STAFF:
            self.store.availability({'staff_id':s['id'],'effective_from':'2026-01-01','revision':0,'windows':{str(i):[{'start':'13:00','end':'21:00'}] for i in range(7)}})

    def tearDown(self):
        self.conn.close()

    def as_user(self,sid=None,manager=True,scope='org:branch',leave=None):
        return Scheduler(self.conn,scope,STAFF,'test-manager' if manager else sid,staff_id=sid,manager=manager,leave=leave,now=datetime(2026,10,29,20,tzinfo=timezone.utc))

    def shift(self,sid='a',shift_id='s1',day='2026-11-03',**changes):
        return {**dict(id=shift_id,staff_id=sid,day=day,start='15:00',end='18:00',kind='Teaching',location='Room 1'),**changes}

    def publish(self,shifts=None):
        shifts=shifts or [self.shift()]
        row=self.store.week(WEEK)
        result=self.store.save({'week_start':WEEK,'revision':row['revision'],'shifts':shifts})
        return self.store.publish({'week_start':WEEK,'revision':result['revision']})

    def request(self,exchange=False):
        self.publish([self.shift(),self.shift('b','s2',day='2026-11-04')])
        return self.as_user('a',False).request_swap({'week_start':WEEK,'source_id':'s1','target_staff_id':'b','target_shift_id':'s2' if exchange else '', 'reason':'Appointment'})['id']

    def accept(self,request_id):
        return self.as_user('b',False).decide_swap({'id':request_id,'revision':1,'action':'accept'})

    def test_selected_week_persists_and_other_week_is_untouched(self):
        self.publish()
        self.assertEqual(self.store.snapshot(NEXT)['shifts'],[])
        self.assertEqual(len(self.store.snapshot(WEEK)['shifts']),1)

    def test_draft_not_exposed_to_staff_or_replacing_published_version(self):
        self.publish()
        self.store.save({'week_start':WEEK,'revision':1,'shifts':[self.shift(end='19:00')]})
        staff=self.as_user('a',False).snapshot(WEEK)
        self.assertEqual(staff['shifts'][0]['end'],'18:00')
        self.assertIsNone(staff['revision'])
        self.assertEqual(staff['audit'],[])

    def test_staff_cannot_save_publish_or_copy(self):
        staff=self.as_user('a',False)
        for action in (staff.save,staff.publish,staff.copy):
            with self.assertRaises(SchedulingDenied):action({})

    def test_other_branch_cannot_read_or_decide_swaps(self):
        request=self.request()
        other=self.as_user(scope='org:other')
        self.assertEqual(other.snapshot(WEEK)['shifts'],[])
        with self.assertRaises(SchedulingError):other.decide_swap({'id':request,'revision':1,'action':'reject','reason':'Attempt'})

    def test_stale_draft_save_is_rejected(self):
        self.publish()
        with self.assertRaises(SchedulingConflict):self.store.save({'week_start':WEEK,'revision':0,'shifts':[]})

    def test_wrong_week_and_non_monday_rejected(self):
        with self.assertRaises(SchedulingError):self.store.week('2026-11-03')
        with self.assertRaises(SchedulingError):self.store.save({'week_start':NEXT,'revision':0,'shifts':[self.shift()]})

    def test_staff_ids_are_strings_not_inline_javascript(self):
        self.publish()
        self.assertEqual(self.store.snapshot(WEEK)['shifts'][0]['staff_id'],'a')

    def test_overlapping_shifts_can_be_drafted_but_not_published(self):
        self.store.save({'week_start':WEEK,'revision':0,'shifts':[self.shift(),self.shift('a','s2')]})
        with self.assertRaisesRegex(SchedulingError,'overlapping'):self.store.publish({'week_start':WEEK,'revision':1})

    def test_unavailable_hours_block_publish(self):
        self.store.availability({'staff_id':'a','effective_from':'2026-01-01','revision':1,'windows':{'1':[]}})
        self.store.save({'week_start':WEEK,'revision':0,'shifts':[self.shift()]})
        with self.assertRaisesRegex(SchedulingError,'outside available'):self.store.publish({'week_start':WEEK,'revision':1})

    def test_unknown_availability_requires_explicit_warning_acceptance(self):
        self.store.availability({'staff_id':'a','effective_from':'2026-01-01','revision':1,'windows':{}})
        self.store.save({'week_start':WEEK,'revision':0,'shifts':[self.shift()]})
        with self.assertRaisesRegex(SchedulingError,'explicitly accept'):self.store.publish({'week_start':WEEK,'revision':1})
        self.store.publish({'week_start':WEEK,'revision':1,'accept_warnings':True})

    def test_approved_leave_blocks_publish(self):
        store=self.as_user(leave=[{'staff_id':'a','start_date':'2026-11-03','end_date':'2026-11-04'}])
        store.save({'week_start':WEEK,'revision':0,'shifts':[self.shift()]})
        with self.assertRaisesRegex(SchedulingError,'approved time off'):store.publish({'week_start':WEEK,'revision':1})

    def test_future_availability_does_not_rewrite_prior_weeks(self):
        self.store.availability({'staff_id':'a','effective_from':'2026-11-09','revision':0,'windows':{'1':[]}})
        self.publish()
        self.assertFalse(self.store.snapshot(WEEK)['checks']['conflicts'])

    def test_staff_can_only_edit_own_availability(self):
        with self.assertRaises(SchedulingDenied):self.as_user('a',False).availability({'staff_id':'b','effective_from':'2026-01-01','revision':1,'windows':{}})

    def test_availability_has_optimistic_revision_check(self):
        with self.assertRaises(SchedulingConflict):self.store.availability({'staff_id':'a','effective_from':'2026-01-01','revision':0,'windows':{}})

    def test_copy_preview_does_not_change_target_and_remaps_dates_ids(self):
        self.publish()
        payload={'source_week':WEEK,'week_start':NEXT,'source_version':'published','mode':'merge','revision':0}
        preview=self.store.copy(payload,True)
        self.assertEqual(self.store.snapshot(NEXT)['shifts'],[])
        self.assertEqual(preview['shifts'][0]['day'],'2026-11-10')
        self.assertNotEqual(preview['shifts'][0]['id'],'s1')
        self.store.copy({**payload,'source_revision':preview['source_revision']})
        self.assertEqual(len(self.store.snapshot(NEXT)['shifts']),1)
        self.assertEqual(self.as_user('a',False).snapshot(NEXT)['shifts'],[])

    def test_copy_merge_deduplicates_exact_shifts(self):
        self.publish()
        p={'source_week':WEEK,'week_start':NEXT,'source_version':'published','mode':'merge','revision':0,'source_revision':1}
        self.store.copy(p);self.store.copy({**p,'revision':1})
        self.assertEqual(len(self.store.snapshot(NEXT)['shifts']),1)

    def test_copy_rejects_source_changes_after_preview(self):
        self.publish()
        p={'source_week':WEEK,'week_start':NEXT,'source_version':'draft','mode':'replace','revision':0,'source_revision':1}
        self.store.save({'week_start':WEEK,'revision':1,'shifts':[]})
        with self.assertRaises(SchedulingConflict):self.store.copy(p)

    def test_copy_requires_confirmation_revision_for_existing_target(self):
        self.publish();self.store.save({'week_start':NEXT,'revision':0,'shifts':[]})
        with self.assertRaises(SchedulingConflict):self.store.copy({'source_week':WEEK,'week_start':NEXT,'source_version':'published','mode':'replace','revision':0,'source_revision':1})

    def test_republish_without_changes_does_not_duplicate_notifications(self):
        self.publish()
        result=self.store.publish({'week_start':WEEK,'revision':1})
        self.assertEqual(result['notifications_created'],0)
        self.assertEqual(len(self.as_user('a',False).snapshot(WEEK)['inbox']),1)

    def test_inbox_is_private_and_acknowledgement_is_recipient_only(self):
        self.publish();a=self.as_user('a',False);notice=a.snapshot(WEEK)['inbox'][0]
        self.assertEqual(self.as_user('b',False).snapshot(WEEK)['inbox'],[])
        with self.assertRaises(SchedulingDenied):self.as_user('b',False).inbox_action({'id':notice['id'],'action':'acknowledge'})
        a.inbox_action({'id':notice['id'],'action':'acknowledge'})
        self.assertTrue(self.store.snapshot(WEEK)['receipts'][0]['acknowledged_at'])

    def test_old_revision_cannot_be_acknowledged_as_current(self):
        self.publish();a=self.as_user('a',False);notice=a.snapshot(WEEK)['inbox'][0]
        self.store.save({'week_start':WEEK,'revision':1,'shifts':[self.shift(end='19:00')]});self.store.publish({'week_start':WEEK,'revision':2})
        with self.assertRaises(SchedulingConflict):a.inbox_action({'id':notice['id'],'action':'acknowledge'})

    def test_cover_requires_colleague_acceptance_then_manager_approval(self):
        request=self.request()
        with self.assertRaisesRegex(SchedulingError,'colleague must accept'):self.store.decide_swap({'id':request,'revision':1,'action':'approve','reason':'Review'})
        self.accept(request)
        with self.assertRaises(SchedulingDenied):self.as_user('a',False).decide_swap({'id':request,'revision':2,'action':'approve','reason':'Attempt'})
        self.store.decide_swap({'id':request,'revision':2,'action':'approve','reason':'Coverage reviewed'})
        result=next(s for s in self.store.snapshot(WEEK)['shifts'] if s['id']=='s1')
        self.assertEqual(result['staff_id'],'b')

    def test_exchange_transfers_both_shifts_atomically(self):
        request=self.request(True);self.accept(request)
        self.store.decide_swap({'id':request,'revision':2,'action':'approve','reason':'Both agreed'})
        shifts={s['id']:s for s in self.store.snapshot(WEEK)['shifts']}
        self.assertEqual((shifts['s1']['staff_id'],shifts['s2']['staff_id']),('b','a'))

    def test_manager_cannot_approve_over_unsaved_publication_draft(self):
        request=self.request();self.accept(request)
        self.store.save({'week_start':WEEK,'revision':1,'shifts':[self.shift(end='19:00')]})
        with self.assertRaisesRegex(SchedulingConflict,'unpublished edits'):self.store.decide_swap({'id':request,'revision':2,'action':'approve','reason':'Attempt'})

    def test_swap_revalidates_changed_availability(self):
        request=self.request();self.accept(request)
        self.store.availability({'staff_id':'b','effective_from':'2026-01-01','revision':1,'windows':{'1':[],'2':[{'start':'13:00','end':'21:00'}]}})
        with self.assertRaisesRegex(SchedulingError,'outside available'):self.store.decide_swap({'id':request,'revision':2,'action':'approve','reason':'Attempt'})
        self.assertEqual(next(s for s in self.store.snapshot(WEEK)['shifts'] if s['id']=='s1')['staff_id'],'a')

    def test_changed_published_shift_invalidates_old_swap(self):
        request=self.request();self.accept(request)
        self.store.save({'week_start':WEEK,'revision':1,'shifts':[self.shift(end='19:00'),self.shift('b','s2',day='2026-11-04')]});self.store.publish({'week_start':WEEK,'revision':2})
        with self.assertRaisesRegex(SchedulingConflict,'changed after submission'):self.store.decide_swap({'id':request,'revision':2,'action':'approve','reason':'Attempt'})

    def test_owner_can_cancel_but_unrelated_staff_cannot_decide(self):
        request=self.request()
        with self.assertRaises(SchedulingDenied):self.as_user('c',False).decide_swap({'id':request,'revision':1,'action':'accept'})
        self.as_user('a',False).decide_swap({'id':request,'revision':1,'action':'cancel'})
        self.assertEqual(self.store.swap_rows()[0]['status'],'cancelled')

    def test_staff_availability_update_notifies_a_manager(self):
        roster=STAFF+[{'id':'m','name':'Manager','role':'office_manager'}]
        staff=Scheduler(self.conn,'org:branch',roster,'Alex',staff_id='a',manager=False,pg=self.store.pg)
        staff.availability({'staff_id':'a','effective_from':'2026-01-01','revision':1,'windows':{}})
        notices=self.conn.execute("SELECT title FROM scheduler_notifications WHERE staff_id='m'").fetchall()
        self.assertEqual(notices[0]['title'],'Staff availability changed')

    def test_staff_cannot_acknowledge_someone_elses_inbox(self):
        self.publish()
        notice=self.as_user('a',False).snapshot(WEEK)['inbox'][0]
        with self.assertRaises(SchedulingDenied):self.store.inbox_action({'id':notice['id'],'action':'read'})

    def test_cover_cannot_double_book_target_staff(self):
        self.publish([self.shift(),self.shift('b','s2')])
        request=self.as_user('a',False).request_swap({'week_start':WEEK,'source_id':'s1','target_staff_id':'b','reason':'Cover'})['id']
        self.accept(request)
        with self.assertRaisesRegex(SchedulingError,'overlapping'):
            self.store.decide_swap({'id':request,'revision':2,'action':'approve','reason':'Attempt'})


class SchedulingRouteTests(unittest.TestCase):
    def test_feature_is_disabled_by_default(self):
        import os
        import app
        from urllib.parse import urlparse
        from unittest.mock import patch
        handler=app.Handler.__new__(app.Handler)
        with patch.dict(os.environ,{'SMP_SCHEDULING_UAT':'0'}),patch.object(handler,'send_json') as send,patch.object(app,'db') as database:
            handler.handle_scheduling(urlparse('/api/scheduling'),False)
            self.assertEqual(send.call_args.args[1],404)
            database.assert_not_called()

    def test_invalid_staff_token_does_not_fall_back_to_local_admin(self):
        import os
        import app
        from urllib.parse import urlparse
        from unittest.mock import patch
        handler=app.Handler.__new__(app.Handler);handler.headers={'Authorization':'Bearer invalid-token'}
        with patch.dict(os.environ,{'SMP_SCHEDULING_UAT':'1'}),patch.object(app,'SUPABASE_REQUIRE_AUTH',False),patch.object(handler,'send_json') as send,patch.object(app,'db') as database:
            handler.handle_scheduling(urlparse('/api/scheduling'),False)
            self.assertEqual(send.call_args.args[1],401)
            database.assert_not_called()


if __name__=='__main__':unittest.main()
