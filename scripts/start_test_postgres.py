"""Start this cloud workspace's disposable PostgreSQL test cluster.

Binaries were downloaded from Debian's signed package repository and extracted
under /workspace; this script never installs system packages or contacts production.
The server listens only on an owner-only Unix socket, not on TCP.
"""
import json
import getpass
from pathlib import Path
import subprocess


def main():
    root=Path('/workspace/.smp-postgres-packages')
    binary=root/'root/usr/lib/postgresql/17/bin'
    if not (binary/'initdb').is_file():
        raise SystemExit('Install verified PostgreSQL 17 tools first; see docs/TEST_AND_PRODUCTION.md.')
    data=root/'test-data-utf8'
    socket=root/'socket'
    socket.mkdir(parents=True,exist_ok=True,mode=0o700)
    if socket.stat().st_mode & 0o077:
        raise SystemExit('Test socket directory must be private (0700).')
    if not (data/'PG_VERSION').exists():
        subprocess.run([str(binary/'initdb'),'-D',str(data),'--no-locale','--encoding=UTF8',
                        '--auth-local=trust','--auth-host=reject'],check=True,stdout=subprocess.DEVNULL)
    status=subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'status'],capture_output=True)
    if status.returncode:
        subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres-utf8.log'),
                        '-o',f"-k {socket} -p 55439 -c listen_addresses=''",'-w','start'],
                       check=True,stdout=subprocess.DEVNULL)
    dsn=f'dbname=postgres user={getpass.getuser()} host={socket} port=55439'
    import psycopg
    with psycopg.connect(dsn) as conn:
        if conn.execute('SHOW server_encoding').fetchone()[0] != 'UTF8':
            raise SystemExit('Test PostgreSQL requires UTF8 encoding.')
        if conn.execute('SHOW listen_addresses').fetchone()[0] != '':
            raise SystemExit('Test PostgreSQL must not expose a TCP listener.')
    print(json.dumps({'ready':True,'SMP_TEST_POSTGRES_DSN':dsn,'bin_path':str(binary)}))


if __name__=='__main__':
    main()
