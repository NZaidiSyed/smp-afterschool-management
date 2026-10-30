"""Start the isolated Schedule Builder UAT on this computer."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8778)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parent
    home=Path(os.environ.get('SMP_SCHEDULING_UAT_HOME',str(Path.home()/'.smp-staff-scheduling-uat'))).resolve()
    env=dict(os.environ,SMP_UAT_HOME=str(home))
    subprocess.run([sys.executable,str(root/'run_attendance_uat.py'),'--prepare-only'],env=env,cwd=root,check=True)
    python=home/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    env.update(SMP_DATA_DIR=str(home/'data'),SMP_BACKUP_DIR=str(home/'backups'),SMP_SCHEDULING_UAT='1')
    subprocess.run([str(python),str(root/'scripts/seed_staff_scheduling_uat.py')],env=env,cwd=root,check=True)
    if not args.prepare_only:
        print(f'Open http://127.0.0.1:{args.port}/staff-scheduler.html on this computer. Production is unchanged.',flush=True)
        subprocess.run([str(python),'-u',str(root/'app.py'),'--host','127.0.0.1','--port',str(args.port)],env=env,cwd=root,check=True)


if __name__=='__main__':
    main()
