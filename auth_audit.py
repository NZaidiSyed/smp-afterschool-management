"""Branch-scoped, server-recorded verified sign-ins; never store bearer tokens."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4


def query(conn, sql, params=(), pg=False):
    return conn.execute(sql.replace('?', '%s') if pg else sql, params)


def ensure_schema(conn, pg=False):
    conn.execute('''CREATE TABLE IF NOT EXISTS auth_sign_ins (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, subject_id TEXT NOT NULL,
        login_id TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL,
        method TEXT NOT NULL, event_kind TEXT NOT NULL,
        session_fingerprint TEXT NOT NULL, verified_at TEXT NOT NULL,
        UNIQUE(scope,session_fingerprint))''')
    conn.execute('CREATE INDEX IF NOT EXISTS auth_sign_ins_scope_time ON auth_sign_ins(scope,verified_at DESC,id DESC)')
    if pg:
        conn.execute('ALTER TABLE auth_sign_ins ENABLE ROW LEVEL SECURITY')
        conn.execute('REVOKE ALL ON TABLE auth_sign_ins FROM PUBLIC')
        for role in ('anon','authenticated'):
            if conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                conn.execute(f'REVOKE ALL ON TABLE auth_sign_ins FROM {role}')


def record_verified(conn, *, scope, subject_id, login_id, display_name, role,
                    method, session_reference, event_kind='session_first_verified', pg=False):
    if not scope or not subject_id or not session_reference:
        raise ValueError('Verified identity and branch context are required')
    if event_kind not in {'session_first_verified','token_first_verified','staff_sign_in'}:
        raise ValueError('Invalid authentication event kind')
    # The scope is also part of the digest so no fingerprint correlates branches.
    fingerprint=hashlib.sha256(json.dumps([scope,method,str(subject_id),session_reference],separators=(',',':')).encode()).hexdigest()
    # Normal API requests reuse a session; avoid taking a write lock each time.
    # The unique constraint still resolves simultaneous first-use requests.
    if query(conn,'SELECT 1 FROM auth_sign_ins WHERE scope=? AND session_fingerprint=?',
             (scope,fingerprint),pg).fetchone():
        return
    query(conn, '''INSERT INTO auth_sign_ins
        (id,scope,subject_id,login_id,display_name,role,method,event_kind,session_fingerprint,verified_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,session_fingerprint) DO NOTHING''',
        (str(uuid4()),scope,str(subject_id),str(login_id)[:320],str(display_name)[:200],str(role)[:80],
         method,event_kind,fingerprint,datetime.now(timezone.utc).isoformat(timespec='microseconds')),pg)


def verified_session_reference(token, subject_id):
    """Call ONLY after the provider accepted this exact bearer token.

    Decoding is not verification. Provider acceptance remains in Handler.require_auth.
    Supabase session_id survives refresh; opaque/legacy tokens get a separate,
    accurately labelled first-token-verification event.
    """
    try:
        body=token.split('.')[1]
        claims=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        session=claims.get('session_id')
        if claims.get('sub') == str(subject_id) and isinstance(session,str) and 0 < len(session) <= 200:
            return session,'session_first_verified'
    except (ValueError,TypeError,IndexError,AttributeError):
        pass
    return hashlib.sha256(token.encode()).hexdigest(),'token_first_verified'


def page(conn, scope, *, limit=50, cursor=None, pg=False):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1 <= limit <= 100:
        raise ValueError('Limit must be between 1 and 100')
    params=[scope]
    where='scope=?'
    if cursor:
        try:
            if len(cursor)>500:raise ValueError()
            moment,identity=json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            parsed=datetime.fromisoformat(moment)
            if parsed.tzinfo is None or not isinstance(identity,str) or len(identity)>40:raise ValueError()
        except (ValueError,TypeError,IndexError,KeyError):
            raise ValueError('Invalid history cursor') from None
        where+=' AND (verified_at<? OR (verified_at=? AND id<?))'
        params.extend([moment,moment,identity])
    params.append(limit+1)
    rows=query(conn,'''SELECT id,subject_id,login_id,display_name,role,method,event_kind,verified_at
        FROM auth_sign_ins WHERE '''+where+' ORDER BY verified_at DESC,id DESC LIMIT ?',params,pg).fetchall()
    more=len(rows)>limit
    result=[dict(row) for row in rows[:limit]]
    next_cursor=None
    if more:
        last=result[-1]
        next_cursor=base64.urlsafe_b64encode(json.dumps([last['verified_at'],last['id']]).encode()).decode().rstrip('=')
    return {'records':result,'next_cursor':next_cursor,'time_basis':'UTC'}
