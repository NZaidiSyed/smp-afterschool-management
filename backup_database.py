import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "kumon_tracking.sqlite3"
DESTINATION = Path("C:/Back/Day")


def backup():
    if not SOURCE.exists():
        raise SystemExit(f"Database not found: {SOURCE}")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = DESTINATION / f"smp_kumon_tracking_{stamp}.sqlite3"
    shutil.copy2(SOURCE, target)
    return target


if __name__ == "__main__":
    print(backup())
