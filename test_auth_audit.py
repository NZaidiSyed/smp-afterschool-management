"""Sign-in history uses only synthetic identities and temporary databases."""
import base64
from contextlib import closing
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError

import app
import auth_audit


def connect(path):
    conn=sqlite3.connect(path);conn.row_factory=sqlite3.Row;return conn


def fake_token(subject,session,nonce='a'):
    body=base64.urlsafe_b64encode(json.dumps({'sub':subject,'session_id':session,'nonce':nonce}).encode()).decode().rstrip('=')
    return 'synthetic.'+body+'.not-a-real-signature'


class AuditStoreTests(unittest.TestCase):
    def setUp(self):
        self.conn=connect(':memory:');self.addCleanup(self.conn.close)
        auth_audit.ensure_schema(self.conn)

    def record(self,scope='org:branch',subject='synthetic',reference='session-1',**values):
        auth_audit.record_verified(self.conn,scope=scope,subject_id=subject,login_id='synthetic@example.invalid',
                                   display_name='Synthetic user',role='Admin',method='supabase',session_reference=reference,**values)
        self.conn.commit()

    def test_refresh_and_repeat_requests_record_one_session(self):
        token1=fake_token('synthetic','session-1','first-token')
        token2=fake_token('synthetic','session-1','refreshed-token')
        for token in [token1,token1,token2]:
            reference,kind=auth_audit.verified_session_reference(token,'synthetic')
            self.record(reference=reference,event_kind=kind)
        rows=auth_audit.page(self.conn,'org:branch')['records']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['event_kind'],'session_first_verified')
        self.assertTrue(rows[0]['verified_at'].endswith('+00:00'))
        self.assertNotIn('session_fingerprint',rows[0])
        stored=json.dumps([dict(r) for r in self.conn.execute('SELECT * FROM auth_sign_ins')])
        self.assertNotIn(token1,stored);self.assertNotIn(token2,stored)

    def test_new_sessions_and_branches_are_separate(self):
        self.record(reference='s1');self.record(reference='s2')
        self.record(scope='other:branch',reference='s1',subject='foreign-user')
        self.assertEqual(len(auth_audit.page(self.conn,'org:branch')['records']),2)
        self.assertEqual(len(auth_audit.page(self.conn,'other:branch')['records']),1)

    def test_opaque_and_mismatched_subject_tokens_are_labelled(self):
        for token in ['opaque-synthetic-token',fake_token('other-user','s1')]:
            reference,kind=auth_audit.verified_session_reference(token,'synthetic')
            self.assertNotEqual(reference,token)
            self.assertEqual(kind,'token_first_verified')

    def test_cursor_pagination_does_not_duplicate_or_leak(self):
        for index in range(5):self.record(reference=str(index))
        self.record(scope='other:branch',subject='private',reference='private')
        collected=[];cursor=None
        while True:
            result=auth_audit.page(self.conn,'org:branch',limit=2,cursor=cursor)
            collected.extend(row['id'] for row in result['records']);cursor=result['next_cursor']
            if not cursor:break
        self.assertEqual(len(collected),5);self.assertEqual(len(set(collected)),5)
        self.assertTrue(all(row['subject_id']!='private' for row in auth_audit.page(self.conn,'org:branch')['records']))

    def test_bad_cursors_and_unbounded_limits_rejected(self):
        for limit in [0,101,-1,True,'50']:
            with self.assertRaises(ValueError):auth_audit.page(self.conn,'org:branch',limit=limit)
        for cursor in ['not-base64','a'*501,base64.urlsafe_b64encode(b'["not-a-time","id"]').decode()]:
            with self.assertRaises(ValueError):auth_audit.page(self.conn,'org:branch',cursor=cursor)

    def test_schema_is_additive_and_repeatable(self):
        self.record();before=auth_audit.page(self.conn,'org:branch')
        auth_audit.ensure_schema(self.conn)
        self.assertEqual(auth_audit.page(self.conn,'org:branch'),before)


class AuditHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='smp-auth-audit-');self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'synthetic.sqlite3'
        with closing(connect(self.path)) as conn:
            conn.executescript('''CREATE TABLE organizations(id INTEGER PRIMARY KEY);
                INSERT INTO organizations VALUES(7);
                CREATE TABLE branches(id INTEGER PRIMARY KEY, organization_id INTEGER,code TEXT);
                INSERT INTO branches VALUES(42,7,'CA-YYC-KMN-0001');
                CREATE TABLE staff_members(id INTEGER PRIMARY KEY,branch_id INTEGER,active INTEGER,
                  staff_name TEXT,email TEXT,role TEXT,role_title TEXT,pin TEXT,pin_hash TEXT,password_hash TEXT);
            ''')
            auth_audit.ensure_schema(conn);conn.commit()
        self.accepted={};self.access_role='Admin'
        for name,value in [('PG_MODE',False),('PRODUCTION',True),('SMP_BRANCH_CODE','CA-YYC-KMN-0001'),
                           ('SMP_ORGANIZATION_ID',''),('SUPABASE_REQUIRE_AUTH',True),
                           ('SUPABASE_URL','https://synthetic-auth.invalid'),('SUPABASE_ANON_KEY','synthetic-public-key')]:
            patcher=patch.object(app,name,value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(app,'db',side_effect=lambda:connect(self.path));patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(app,'ensure_first_admin');patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(app,'get_user_access',side_effect=lambda conn,user:{'email':user['email'],'role':self.access_role,'active':True})
        patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(app,'urlopen',side_effect=self.provider);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(app.Handler,'log_message');patcher.start();self.addCleanup(patcher.stop)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def provider(self,request,**kwargs):
        from io import BytesIO
        token=request.get_header('Authorization','').removeprefix('Bearer ')
        if token not in self.accepted:raise HTTPError(request.full_url,401,'Synthetic invalid token',{},None)
        body=BytesIO(json.dumps(self.accepted[token]).encode());body.status=200;return body

    def token(self,subject='admin',session='s1',nonce='a'):
        token=fake_token(subject,session,nonce)
        self.accepted[token]={'id':subject,'email':subject+'@example.invalid',
                              'email_confirmed_at':'2026-01-01T00:00:00Z','user_metadata':{'name':'Synthetic '+subject}}
        return token

    def request(self,path='/api/auth/history',token=None,method='GET',payload=None):
        conn=http.client.HTTPConnection(*self.server.server_address)
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        conn.request(method,path,headers=headers,body=json.dumps(payload) if payload is not None else None)
        response=conn.getresponse();body=response.read();status=response.status;conn.close()
        try:return status,json.loads(body)
        except ValueError:return status,{}

    def records(self):
        with closing(connect(self.path)) as conn:return [dict(r) for r in conn.execute('SELECT * FROM auth_sign_ins')]

    def test_admin_and_owner_can_read_but_ordinary_roles_cannot(self):
        for role in ['Admin','Owner']:
            self.access_role=role
            status,data=self.request(token=self.token(subject=role,session=role))
            self.assertEqual(status,200);self.assertEqual(data['branch_code'],'CA-YYC-KMN-0001')
        for role in ['Office Manager','Office Assistant','Staff']:
            self.access_role=role
            self.assertEqual(self.request(token=self.token(subject=role,session=role))[0],403)

    def test_anonymous_invalid_and_forged_session_attempts_create_no_log(self):
        self.assertEqual(self.request()[0],401)
        self.assertEqual(self.request(token=fake_token('admin','forged'))[0],401)
        self.assertEqual(self.records(),[])

    def test_authorized_repeated_requests_and_refresh_are_one_event(self):
        self.assertEqual(self.request(token=self.token())[0],200)
        self.assertEqual(self.request(token=self.token(nonce='refreshed'))[0],200)
        self.assertEqual(len(self.records()),1)
        self.assertEqual(self.request(token=self.token(session='new-session'))[0],200)
        self.assertEqual(len(self.records()),2)

    def test_parallel_first_requests_create_one_session_event(self):
        token=self.token()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.request(token=token)[0],range(4)))
        self.assertEqual(results,[200]*4)
        self.assertEqual(len(self.records()),1)

    def test_foreign_branch_scope_cannot_be_selected_by_query(self):
        with closing(connect(self.path)) as conn:
            auth_audit.record_verified(conn,scope='foreign:branch',subject_id='foreign',login_id='private@example.invalid',display_name='Private',role='Owner',method='supabase',session_reference='foreign')
            conn.commit()
        status,data=self.request('/api/auth/history?scope=foreign:branch&branch_id=99',token=self.token())
        self.assertEqual(status,200)
        self.assertNotIn('private@example.invalid',json.dumps(data))

    def test_denied_application_membership_does_not_record_a_success(self):
        with patch.object(app,'get_user_access',return_value=None):
            self.assertEqual(self.request(token=self.token())[0],403)
        self.assertEqual(self.records(),[])

    def test_profile_metadata_cannot_impersonate_an_account_email(self):
        token=self.token();self.accepted[token].pop('email')
        self.accepted[token]['user_metadata']['email']='owner@example.invalid'
        self.assertEqual(self.request(token=token)[0],403)
        self.assertEqual(self.records(),[])

    def test_unconfirmed_email_cannot_gain_access_or_a_successful_audit_entry(self):
        token=self.token();self.accepted[token]['email_confirmed_at']=None
        self.assertEqual(self.request(token=token)[0],403)
        self.assertEqual(self.records(),[])

    def test_history_store_failure_blocks_access_without_misreporting_bad_password(self):
        with patch.object(app.auth_audit,'record_verified',side_effect=RuntimeError('Synthetic unavailable store')):
            status,data=self.request(token=self.token())
        self.assertEqual(status,503);self.assertNotIn('Synthetic unavailable store',json.dumps(data))
        self.assertEqual(self.records(),[])

    def test_successful_staff_password_login_is_audited_but_bad_password_is_not(self):
        hashed=app.hash_bcrypt('Synthetic-test-password')
        with closing(connect(self.path)) as conn:
            conn.execute("INSERT INTO staff_members VALUES(8,42,1,'Synthetic Staff','staff@example.invalid','staff','Teaching','',NULL,?)",(hashed,));conn.commit()
        status,data=self.request('/api/staffbase/auth/password',method='POST',payload={'email':'staff@example.invalid','password':'wrong'})
        self.assertEqual(status,401);self.assertEqual(self.records(),[])
        status,data=self.request('/api/staffbase/auth/password',method='POST',payload={'email':'staff@example.invalid','password':'Synthetic-test-password'})
        self.assertEqual(status,200)
        rows=self.records();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['event_kind'],'staff_sign_in')
        self.assertEqual(rows[0]['method'],'staff_password');self.assertEqual(rows[0]['scope'],'local:42')
        self.assertNotIn(data['token'],json.dumps(rows));self.assertNotIn('Synthetic-test-password',json.dumps(rows))

    def test_staff_pin_entrypoints_record_success_and_fail_closed_without_audit_storage(self):
        hashed=app.hash_bcrypt('983017')
        with closing(connect(self.path)) as conn:
            conn.execute("INSERT INTO staff_members VALUES(8,42,1,'Synthetic Staff','staff@example.invalid','staff','Teaching','',?,NULL)",(hashed,));conn.commit()
        for path in ['/api/staff/auth','/api/staffbase/auth/pin']:
            self.assertEqual(self.request(path,method='POST',payload={'staffId':8,'pin':'wrong'})[0],401)
            self.assertEqual(self.request(path,method='POST',payload={'staffId':8,'pin':'983017'})[0],200)
        self.assertEqual(len(self.records()),2)
        self.assertTrue(all(row['method']=='staff_pin' for row in self.records()))
        with patch.object(app.auth_audit,'record_verified',side_effect=RuntimeError('Synthetic unavailable store')):
            status,data=self.request('/api/staff/auth',method='POST',payload={'staffId':8,'pin':'983017'})
        self.assertEqual(status,503);self.assertNotIn('token',data);self.assertEqual(len(self.records()),2)


if __name__=='__main__':unittest.main()
