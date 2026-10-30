"""Prepare and run the local attendance UAT build without production connections."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8771)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        raise SystemExit('Install Python 3.10 or newer; Python 3.12 is recommended.')
    if any(os.environ.get(k) for k in ['DATABASE_URL', 'SUPABASE_DB_URL', 'SUPABASE_URL', 'SUPABASE_ANON_KEY']):
        raise SystemExit('Use a clean terminal without production database/Supabase variables for local UAT.')
    if os.environ.get('SUPABASE_REQUIRE_AUTH', '').lower() in {'1','true','yes','on'}:
        raise SystemExit('Use a clean terminal without production authentication enabled for local UAT.')
    root = Path(__file__).resolve().parent
    home = Path(os.environ.get('SMP_UAT_HOME', str(Path.home() / '.smp-attendance-uat'))).resolve()
    home.mkdir(parents=True, exist_ok=True)
    environment = home / 'venv'
    executable = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(executable), '-m', 'pip', 'install', '-r', str(root/'requirements.txt')], check=True, cwd=root)
    settings = dict(os.environ, SMP_DATA_DIR=str(home/'data'), SMP_BACKUP_DIR=str(home/'backups'), SUPABASE_REQUIRE_AUTH='0')
    subprocess.run([str(executable), str(root/'scripts/seed_attendance_uat.py')], cwd=root, env=settings, check=True)
    print(f'UAT data: {home / "data"}', flush=True)
    print('Synthetic data only. Do not expose this development server publicly.', flush=True)
    if not args.prepare_only:
        print(f'Open http://127.0.0.1:{args.port}/?attendance=1 in your browser. Ctrl+C stops the server.', flush=True)
        subprocess.run([str(executable), '-u', str(root/'app.py'), '--host', '127.0.0.1', '--port', str(args.port)], cwd=root, env=settings, check=True)


if __name__ == '__main__':
    main()
