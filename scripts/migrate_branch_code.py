"""Read-only preflight by default; rename one branch without changing its ID.

Run from the repository root. PostgreSQL needs a direct, securely supplied
SMP_MIGRATION_DATABASE_URL and pg_dump/pg_restore for its mandatory backup.
Never pass a credential-bearing database URL on the command line.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from branch_security import isolated_branch, validate_branch_code


def postgres_client_environment(dsn):
    """Pass connection details without putting credentials in process arguments.

    PGDATABASE alone does not expand a complete connection string for pg_dump.
    Reject unknown options instead of silently dropping connection protections.
    """
    from psycopg.conninfo import conninfo_to_dict
    names = {
        'dbname':'PGDATABASE', 'host':'PGHOST', 'hostaddr':'PGHOSTADDR',
        'port':'PGPORT', 'user':'PGUSER', 'password':'PGPASSWORD',
        'passfile':'PGPASSFILE', 'service':'PGSERVICE', 'options':'PGOPTIONS',
        'application_name':'PGAPPNAME', 'client_encoding':'PGCLIENTENCODING',
        'connect_timeout':'PGCONNECT_TIMEOUT', 'channel_binding':'PGCHANNELBINDING',
        'sslmode':'PGSSLMODE', 'sslcert':'PGSSLCERT', 'sslkey':'PGSSLKEY',
        'sslrootcert':'PGSSLROOTCERT', 'sslcrl':'PGSSLCRL', 'sslcrldir':'PGSSLCRLDIR',
        'ssl_min_protocol_version':'PGSSLMINPROTOCOLVERSION',
        'ssl_max_protocol_version':'PGSSLMAXPROTOCOLVERSION',
        'sslsni':'PGSSLSNI', 'gssencmode':'PGGSSENCMODE', 'krbsrvname':'PGKRBSRVNAME',
        'target_session_attrs':'PGTARGETSESSIONATTRS', 'load_balance_hosts':'PGLOADBALANCEHOSTS',
    }
    options = conninfo_to_dict(dsn)
    if set(options) - names.keys():
        raise ValueError('Backup connection contains unsupported libpq options')
    return dict(os.environ, **{names[key]:value for key,value in options.items()})


def inventory(conn, pg=False):
    """Fingerprint every public application row; never print row contents."""
    if pg:
        from psycopg import sql
        names = [r['tablename'] for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    else:
        names = [r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    result = {}
    for name in names:
        if pg:
            rows = conn.execute(sql.SQL('SELECT * FROM public.{}').format(sql.Identifier(name))).fetchall()
        else:
            rows = conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
        hashes = []
        for row in rows:
            data = dict(row)
            if name == 'branches':
                data.pop('code', None)  # This is the only permitted record change.
            hashes.append(hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest())
        result[name] = {'rows': len(rows), 'sha256': hashlib.sha256(''.join(sorted(hashes)).encode()).hexdigest()}
    return result


def rename(conn, expected_id, old_code, new_code, pg=False):
    validate_branch_code(new_code)
    branch = isolated_branch(conn, pg)
    if str(branch['id']) != str(expected_id):
        raise ValueError('Branch ID differs from the explicitly reviewed branch')
    if branch['code'] not in (old_code, new_code):
        raise ValueError('Existing branch code differs from the reviewed source code')
    before = inventory(conn, pg)
    if branch['code'] == new_code:
        return {'status': 'already_applied', 'branch_id': str(expected_id), 'code': new_code, 'inventory': before}
    marker = '%s' if pg else '?'
    conn.execute(f'UPDATE branches SET code={marker} WHERE id={marker} AND code={marker}',
                 (new_code, expected_id, old_code))
    if inventory(conn, pg) != before:
        raise RuntimeError('Record integrity check failed; transaction must be rolled back')
    actual = isolated_branch(conn, pg, new_code)
    if str(actual['id']) != str(expected_id):
        raise RuntimeError('Branch ID changed unexpectedly')
    return {'status': 'renamed', 'branch_id': str(expected_id), 'old_code': old_code, 'code': new_code, 'inventory': before}


def backup(conn, directory, pg=False):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ValueError('Backup directory must be private (permissions 0700)')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = directory / ('branch-pre-migration-' + stamp + ('.dump' if pg else '.sqlite3'))
    # Reserve an owner-only destination without overwriting anything.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        if pg:
            env = postgres_client_environment(os.environ['SMP_MIGRATION_DATABASE_URL'])
            subprocess.run(['pg_dump', '--format=custom', '--no-password', '--file', str(target)],
                           env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(['pg_restore', '--list', str(target)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            with closing(sqlite3.connect(target)) as copy:
                conn.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
            with closing(sqlite3.connect(target)) as restored:
                restored.row_factory = sqlite3.Row
                if inventory(restored) != inventory(conn):
                    raise RuntimeError('Restored backup does not match the source')
        if target.stat().st_size == 0:
            raise RuntimeError('Backup is empty')
        return str(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise RuntimeError('Backup could not be created and checked; no branch rename was attempted') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--sqlite', type=Path)
    choice.add_argument('--postgres', action='store_true')
    parser.add_argument('--expected-branch-id', required=True)
    parser.add_argument('--old-code', default='CSC0001')
    parser.add_argument('--new-code', default='CA-YYC-KMN-0001')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    validate_branch_code(args.new_code)
    if args.postgres:
        import psycopg
        from psycopg.rows import dict_row
        dsn = os.environ.get('SMP_MIGRATION_DATABASE_URL')
        if not dsn:
            raise ValueError('Supply SMP_MIGRATION_DATABASE_URL securely in environment settings')
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
    else:
        path = args.sqlite.resolve()
        if not path.is_file():
            raise ValueError('SQLite database must already exist')
        conn = sqlite3.connect(path.as_uri() + ('?mode=rw' if args.apply else '?mode=ro'), uri=True)
        conn.row_factory = sqlite3.Row
    with closing(conn):
        with (conn.transaction() if args.postgres else conn):
            if args.postgres and not args.apply:
                conn.execute('SET TRANSACTION READ ONLY')
            branch = isolated_branch(conn, args.postgres)
            if str(branch['id']) != args.expected_branch_id or branch['code'] not in (args.old_code, args.new_code):
                raise ValueError('Branch identity does not match the reviewed migration')
            if not args.apply:
                print(json.dumps({'status': 'preflight_only', 'branch_id': str(branch['id']),
                                  'old_code': branch['code'], 'new_code': args.new_code,
                                  'inventory': inventory(conn, args.postgres)}, indent=2))
                return
        if not args.backup_dir:
            raise ValueError('--backup-dir is required for --apply')
        backup_path = backup(conn, args.backup_dir, args.postgres)
        conn.commit()
        with (conn.transaction() if args.postgres else conn):
            if args.postgres:
                from psycopg import sql
                tables = [r['tablename'] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
                for name in tables:
                    conn.execute(sql.SQL('LOCK TABLE public.{} IN SHARE ROW EXCLUSIVE MODE').format(sql.Identifier(name)))
            else:
                conn.execute('BEGIN IMMEDIATE')
            report = rename(conn, args.expected_branch_id, args.old_code, args.new_code, args.postgres)
        report['backup'] = backup_path
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # DB driver errors may contain credentials or student data. Do not emit them.
        if isinstance(exc, (ValueError, RuntimeError)):
            print(str(exc), file=sys.stderr)
        else:
            print('Migration failed; review database permissions and connectivity securely.', file=sys.stderr)
        sys.exit(1)
