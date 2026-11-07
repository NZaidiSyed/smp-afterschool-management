"""Reproducible local test workflow with synthetic data outside the checkout.

This does not create a hosted test service, contact production, or use a real
Supabase project. Each invocation uses its own retained UAT directory.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import sqlite3
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    forbidden = ('DATABASE_URL', 'SUPABASE_DB_URL', 'SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SMP_BRANCH_CODE', 'SENDGRID_API_KEY', 'SMTP_HOST', 'SMTP_SERVER')
    if any(os.environ.get(name) for name in forbidden) or os.environ.get('SUPABASE_REQUIRE_AUTH', '').lower() in {'1','true','yes','on'} or os.environ.get('RENDER') == 'true' or os.environ.get('SMP_ENV') == 'production':
        raise SystemExit('Run local synthetic checks without production database/authentication variables.')
    home = Path(tempfile.mkdtemp(prefix='smp-security-test-uat-', dir='/workspace'))
    env = dict(os.environ, SMP_SCHEDULING_UAT_HOME=str(home))
    subprocess.run([sys.executable, str(root/'run_staff_scheduling_uat.py'), '--prepare-only'], cwd=root, env=env, check=True)
    python = home/'venv/bin/python'
    env.update(SMP_DATA_DIR=str(home/'data'), SMP_BACKUP_DIR=str(home/'backups'), SMP_SCHEDULING_UAT='1')
    # This DB was just created by this invocation and contains synthetic records.
    # Rehearse the same reviewed code-only migration intended for production.
    database = home/'data/kumon_tracking.sqlite3'
    with sqlite3.connect(database) as conn:
        branches = conn.execute('SELECT id FROM branches').fetchall()
        if len(branches) != 1:
            raise RuntimeError('Synthetic fixture must contain exactly one branch')
        identity = str(branches[0][0])
        conn.execute("UPDATE branches SET code='CSC0001' WHERE id=?", (identity,))
    backups = home/'migration-backups'
    subprocess.run([str(python), str(root/'scripts/migrate_branch_code.py'), '--sqlite', str(database),
                    '--expected-branch-id', identity, '--apply', '--backup-dir', str(backups)],
                   cwd=root, env=env, check=True)
    env['SMP_BRANCH_CODE'] = 'CA-YYC-KMN-0001'
    subprocess.run([str(python), '-m', 'unittest', 'discover', '-v'], cwd=root, env=env, check=True)
    for script in ('test_service_worker.cjs','test_camera_scanner.cjs'):
        subprocess.run(['node', str(root/'scripts'/script)], cwd=root, env=env, check=True)
    if args.browser:
        base = f'http://127.0.0.1:{args.port}'
        # Never run tests against an unrelated process that already owns the port.
        import socket
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',args.port)) == 0:
                raise SystemExit('Test port is already in use; select another port.')
        log = home/'server.log'
        with log.open('w') as output:
            process = subprocess.Popen([str(python), '-u', str(root/'app.py'), '--host','127.0.0.1','--port',str(args.port)],
                                       cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                for attempt in range(100):
                    if process.poll() is not None:
                        raise RuntimeError(f'Test server failed; inspect {log}')
                    try:
                        with urlopen(base+'/api/healthz',timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError(f'Test server did not become ready; inspect {log}')
                with urlopen(base+'/api/config',timeout=5) as response:
                    config=json.load(response)
                if not config.get('test_mode'):
                    raise RuntimeError('Synthetic server must identify itself as a test environment')
                with urlopen(base+'/api/bootstrap',timeout=10) as response:
                    bootstrap=json.load(response)
                if bootstrap.get('settings',{}).get('branch_code') != 'CA-YYC-KMN-0001' or not bootstrap.get('students'):
                    raise RuntimeError('Migrated branch identity and synthetic students did not survive startup')
                subprocess.run(['node',str(root/'scripts/test_staff_scheduler_browser.cjs')],cwd=root,
                               env=dict(env,SMP_SCHEDULER_TEST_URL=base),check=True)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait()
    print(f'PASS: independent synthetic test data retained at {home}')


if __name__ == '__main__':
    main()
