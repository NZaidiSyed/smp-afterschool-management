"""Release-policy regressions run in disposable Git repositories, never production.

Approval/deployment examples below are explicitly synthetic test records. They
are never written to the real master/register and never call a deployment API.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import release_guard as guard


class ReleaseGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='smp-release-policy-test-')
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        (self.root/'docs/releases').mkdir(parents=True)
        (self.root/'AGENTS.md').write_text('Synthetic test policy only\n')
        (self.root/'docs/RELEASE_POLICY.md').write_text('Synthetic test policy only\n')
        (self.root/'APP_MASTER.md').write_text('Synthetic test master\n'+guard.START+guard.END+'\n')
        (self.root/'app.py').write_text('# Synthetic source; no production connections\n')
        self.identity='SMP-20261107-999'
        self.sha='0'*40
        self.data={'schema_version':1,'active_release_id':self.identity,'releases':[{
            'id':self.identity,'title':'Synthetic fixture only','scope':'Test policy checks, not a real release',
            'candidate_sha':self.sha,'state':'planned','rollback_plan':'Synthetic rollback fixture only',
            'checks':{name:{'status':'pending','evidence':'Synthetic check not run'} for name in guard.REQUIRED_GATES},
            'events':[self.event('planned')]}]}
        self.write()
        self.git('init','--quiet')
        self.git('add','.')
        self.git('-c','user.name=Synthetic Test','-c','user.email=synthetic@example.invalid','commit','--quiet','-m','Synthetic release baseline')
        self.sha=self.git('rev-parse','HEAD').strip()
        self.data['releases'][0]['candidate_sha']=self.sha
        self.data['releases'][0]['events'][0]['candidate_sha']=self.sha
        self.write()

    def git(self,*args):
        return subprocess.check_output(['git','-C',str(self.root),*args],text=True,stderr=subprocess.DEVNULL)

    def event(self,state,**extra):
        return dict(state=state,actor='Synthetic unit test fixture',reference='synthetic-fixture-only',
                    summary='Synthetic evidence; no real UAT approval or deployment',candidate_sha=self.sha,
                    recorded_at=datetime.now(timezone.utc).isoformat(),**extra)

    def write(self):
        (self.root/guard.REGISTER).write_text(json.dumps(self.data,indent=2)+'\n')
        guard.sync(self.root,self.data)

    def prepare_uat(self):
        guard.record(self.root,self.identity,self.event('testing'))
        checks={name:{'status':'passed','evidence':'Synthetic passing fixture only','candidate_sha':self.sha} for name in guard.REQUIRED_GATES}
        guard.record(self.root,self.identity,self.event('awaiting_uat',checks=checks))

    def approve(self):
        approval=dict(approver='Synthetic approver; test only',approved_at=datetime.now(timezone.utc).isoformat(),
                      reference='synthetic-approval-only',decision='approved_for_production',release_id=self.identity,
                      candidate_sha=self.sha)
        return guard.record(self.root,self.identity,self.event('approved',approval=approval))

    def test_registered_master_and_candidate_validate(self):
        guard.validate(self.root,True)

    def test_no_approval_means_no_production(self):
        with self.assertRaisesRegex(ValueError,'no current UAT approval'):
            guard.production_ready(self.root,guard.validate(self.root),self.identity)

    def test_bare_approval_state_without_explicit_user_record_is_rejected(self):
        self.prepare_uat()
        before=(self.root/guard.REGISTER).read_text()
        with self.assertRaisesRegex(ValueError,'approval is missing'):
            guard.record(self.root,self.identity,self.event('approved'))
        self.assertEqual((self.root/guard.REGISTER).read_text(),before)

    def test_all_gates_are_required_and_cannot_be_removed(self):
        del self.data['releases'][0]['checks']['hosted_uat']
        with self.assertRaisesRegex(ValueError,'Mandatory production gates'):
            guard.validate_data(self.data)

    def test_failed_gate_blocks_approval_even_with_user_record(self):
        self.prepare_uat()
        data=guard.load(self.root)
        data['releases'][0]['checks']['hosted_uat']['status']='failed'
        (self.root/guard.REGISTER).write_text(json.dumps(data))
        guard.sync(self.root,data)
        with self.assertRaisesRegex(ValueError,'hosted_uat'):
            self.approve()

    def test_stale_test_evidence_blocks_approval(self):
        self.prepare_uat()
        data=guard.load(self.root)
        data['releases'][0]['checks']['automated_tests']['candidate_sha']='1'*40
        (self.root/guard.REGISTER).write_text(json.dumps(data));guard.sync(self.root,data)
        with self.assertRaisesRegex(ValueError,'different candidate'):
            self.approve()

    def test_recorded_approval_updates_master_but_does_not_mark_deployed(self):
        self.prepare_uat();data=self.approve()
        self.assertEqual(data['releases'][0]['state'],'approved')
        self.assertIn('synthetic-approval-only',(self.root/guard.MASTER).read_text())
        self.assertNotIn('**deployed**',(self.root/guard.MASTER).read_text())
        guard.production_ready(self.root,guard.validate(self.root),self.identity)

    def test_changed_source_invalidates_prior_approval(self):
        self.prepare_uat();self.approve()
        (self.root/'app.py').write_text('# Changed after approval\n')
        with self.assertRaisesRegex(ValueError,'Candidate changed after freeze'):
            guard.production_ready(self.root,guard.validate(self.root),self.identity)

    def test_new_untracked_source_invalidates_prior_approval(self):
        self.prepare_uat();self.approve()
        (self.root/'new_runtime.py').write_text('# New code\n')
        with self.assertRaisesRegex(ValueError,'Candidate changed after freeze'):
            guard.production_ready(self.root,guard.validate(self.root),self.identity)

    def test_changed_master_rules_invalidate_prior_approval(self):
        self.prepare_uat();self.approve()
        master=self.root/guard.MASTER
        master.write_text(master.read_text().replace('Synthetic test master','Changed policy'))
        with self.assertRaisesRegex(ValueError,'Master policy/facts changed'):
            guard.production_ready(self.root,guard.validate(self.root),self.identity)

    def test_stale_master_history_is_detected_and_repaired_without_losing_facts(self):
        master=self.root/guard.MASTER
        master.write_text(master.read_text().replace('Active release:','Stale active release:'))
        with self.assertRaisesRegex(ValueError,'history is stale'):
            guard.validate(self.root)
        guard.sync(self.root,guard.load(self.root))
        self.assertTrue(master.read_text().startswith('Synthetic test master'))
        guard.validate(self.root)

    def test_deployment_requires_start_and_live_evidence_and_records_master(self):
        self.prepare_uat();self.approve()
        with self.assertRaises(ValueError):
            guard.record(self.root,self.identity,self.event('deployed'))
        guard.record(self.root,self.identity,self.event('deployment_started',target='Synthetic service/database, never real'))
        with self.assertRaisesRegex(ValueError,'evidence is missing'):
            guard.record(self.root,self.identity,self.event('deployed'))
        deployment=dict(environment='production',service='synthetic-test-service-not-real',reference='synthetic-deploy-only',
                        candidate_sha=self.sha,verification='Synthetic verified test fixture; no provider calls')
        guard.record(self.root,self.identity,self.event('deployed',deployment=deployment))
        self.assertIn('synthetic-deploy-only',(self.root/guard.MASTER).read_text())
        self.assertEqual(guard.validate(self.root)['releases'][0]['state'],'deployed')

    def test_failure_is_preserved_instead_of_claiming_success(self):
        self.prepare_uat();self.approve()
        guard.record(self.root,self.identity,self.event('deployment_started',target='Synthetic target only'))
        guard.record(self.root,self.identity,self.event('deployment_failed'))
        data=guard.validate(self.root)
        self.assertEqual(data['releases'][0]['state'],'deployment_failed')
        self.assertEqual(len(data['releases'][0]['events']),6)
        self.assertNotIn('**deployed**',(self.root/guard.MASTER).read_text())


if __name__=='__main__':
    unittest.main()
