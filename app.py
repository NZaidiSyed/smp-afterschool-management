import json
import re
import shutil
import sqlite3
import sys
import zipfile
import csv
import io
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "Kumon_Tracking_FINAL-1.xlsm"
DB = ROOT / "kumon_tracking.sqlite3"
BACKUP_DIR = Path("C:/Back/Day")
DEFAULT_SETTINGS = {
    "institution_name": "SMP - After School Management Program",
    "institution_phone": "",
    "institution_details": "After-school enrolment, student roster, fee tracking, and monthly collection dashboard.",
    "current_month": "May-26",
    "subjects_offered": "Math\nEnglish",
}

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

MONTHS = [
    "Apr-25", "May-25", "Jun-25", "Jul-25", "Aug-25", "Sep-25", "Oct-25", "Nov-25", "Dec-25",
    "Jan-26", "Feb-26", "Mar-26", "Apr-26", "May-26", "Jun-26", "Jul-26", "Aug-26", "Sep-26",
    "Oct-26", "Nov-26", "Dec-26", "Jan-27", "Feb-27", "Mar-27", "Apr-27", "May-27", "Jun-27",
    "Jul-27", "Aug-27", "Sep-27", "Oct-27", "Nov-27", "Dec-27",
]

FORMULA_MANIFEST = {
    "source_workbook_tabs": ["Dashboard", "Fee Tracker", "Student Roster", "Settings", "Batch Entry"],
    "editable_tabs": ["Student Roster", "Batch Entry"],
    "read_only_tabs": ["Dashboard"],
    "student_roster": {
        "standard_monthly_fee": "Settings lookup by Subjects + Rate Type, matching workbook rate schedule.",
        "totals": {
            "active_students": 'COUNTIFS(Status,"C",Subjects,"<>")',
            "total_enrolment": 'Math + English + Both*2 for current students',
            "monthly_fee_total": 'SUMIFS(STD Monthly Fee, Status,"C", Subjects,"<>")',
        },
    },
    "fee_tracker": {
        "subject_units": 'IF(UPPER(Subjects)="BOTH",2,IF(Math or English,1,0))',
        "total_paid": "SUM(Apr-25:Dec-27)",
        "balance": 'IF(LOWER(Status)="c",MAX(0,STD Fee - Total Paid),0)',
        "editable_fields": "Monthly payment columns only. Student identity and standard fee fields are frozen from Student Roster.",
    },
    "dashboard": {
        "active_students": 'COUNTIFS(Fee Tracker Status,"C", Subjects,"<>")',
        "may_2026_revenue": 'SUMIF(Fee Tracker Status,"C", May-26)',
        "annual_projected_revenue": 'SUMIFS(STD Fee, Status,"C", Subjects,"<>") * 12',
        "payment_method_cards": "SUMIFS current month by PAD, E-transfer/E-Transfer, Cash/Credit Card",
        "monthly_table": "SUMIF/SUMIFS/COUNTIFS across monthly Fee Tracker columns",
    },
    "vba_equivalent": {
        "AddOrUpdateStudent": "Handled by Student Roster form: create/update student, recalculate fee from settings, sync Fee Tracker.",
        "SaveBatchStudents": "Handled by Batch Entry: validates required fields, inserts up to 10 records, syncs Fee Tracker.",
        "ClearBatchForm": "Handled in browser after successful batch save.",
        "FindTotalsRow": "Not needed in database; totals are calculated from queries.",
    },
    "product_roadmap": {
        "authentication": "Production version should use Google OAuth or email magic-link login.",
        "trial_and_billing": "14-day free trial, then recurring 20 CAD subscription through Stripe.",
        "roles": "One Admin role can edit/delete records; staff users can view and update permitted operational fields only.",
        "backup": "SQLite database can be backed up weekly to C:/Back/Day when filesystem permission is granted.",
    },
}


def col_to_num(col):
    total = 0
    for ch in col:
        total = total * 26 + ord(ch) - 64
    return total


def excel_date(value):
    if value in (None, ""):
        return ""
    try:
        serial = float(value)
    except (TypeError, ValueError):
        return str(value)
    if serial <= 0:
        return str(value)
    return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%Y-%m-%d")


def money(value):
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class WorkbookReader:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        self.shared = self._shared_strings()
        self.sheets = self._sheet_paths()

    def _shared_strings(self):
        strings = []
        root = ET.fromstring(self.zip.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            strings.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        return strings

    def _sheet_paths(self):
        workbook = ET.fromstring(self.zip.read("xl/workbook.xml"))
        rels = ET.fromstring(self.zip.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("pr:Relationship", NS)}
        paths = {}
        for sheet in workbook.find("m:sheets", NS).findall("m:sheet", NS):
            rid = sheet.attrib[f"{{{NS['r']}}}id"]
            paths[sheet.attrib["name"]] = "xl/" + relmap[rid].lstrip("/")
        return paths

    def cells(self, sheet_name):
        root = ET.fromstring(self.zip.read(self.sheets[sheet_name]))
        values = {}
        formulas = {}
        for cell in root.findall(".//m:c", NS):
            ref = cell.attrib.get("r")
            cell_type = cell.attrib.get("t")
            formula = cell.find("m:f", NS)
            raw = cell.find("m:v", NS)
            value = None
            if raw is not None:
                value = raw.text
                if cell_type == "s":
                    value = self.shared[int(value)]
            if formula is not None:
                formulas[ref] = formula.text or ""
            if value is not None:
                values[ref] = value
        return values, formulas


def cell(values, col, row):
    return values.get(f"{col}{row}", "")


def create_empty_db():
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            number INTEGER,
            student_name TEXT NOT NULL,
            parent_guardian TEXT,
            status TEXT NOT NULL DEFAULT 'C',
            enrol_date TEXT,
            subjects TEXT NOT NULL,
            rate_type TEXT,
            std_monthly_fee REAL NOT NULL DEFAULT 0,
            payment_method TEXT,
            phone TEXT,
            email TEXT,
            siblings TEXT,
            notes TEXT,
            last_modification TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            month_label TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            UNIQUE(student_id, month_label)
        );
        CREATE TABLE rates (
            id INTEGER PRIMARY KEY,
            subject TEXT NOT NULL,
            rate_type TEXT NOT NULL,
            monthly_fee REAL NOT NULL,
            description TEXT,
            UNIQUE(subject, rate_type)
        );
        CREATE TABLE app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'staff',
            auth_provider TEXT NOT NULL DEFAULT 'local',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'trialing',
            trial_start TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            trial_end TEXT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            cancel_requested_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE discount_codes (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            description TEXT,
            percent_off REAL NOT NULL DEFAULT 0,
            amount_off REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE payer_aliases (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, alias)
        );
        CREATE TABLE payment_imports (
            id INTEGER PRIMARY KEY,
            file_name TEXT,
            source TEXT,
            imported_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE payment_import_rows (
            id INTEGER PRIMARY KEY,
            import_id INTEGER REFERENCES payment_imports(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
            transaction_date TEXT,
            description TEXT,
            amount REAL NOT NULL DEFAULT 0,
            source TEXT,
            month_label TEXT,
            match_score INTEGER NOT NULL DEFAULT 0,
            match_status TEXT NOT NULL DEFAULT 'review',
            notes TEXT,
            applied_at TEXT
        );
        """
    )
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT INTO app_meta(key,value) VALUES (?,?)", (key, value))
    conn.execute("INSERT INTO app_meta(key,value) VALUES (?,?)", ("formula_manifest", json.dumps(FORMULA_MANIFEST)))
    for subject in ["Math", "English"]:
        conn.execute(
            "INSERT OR IGNORE INTO rates(subject, rate_type, monthly_fee, description) VALUES (?,?,?,?)",
            (subject, "Regular", 165, "Default starter rate"),
        )
    cur = conn.execute(
        "INSERT INTO users(email, display_name, role, auth_provider) VALUES (?,?,?,?)",
        ("admin@local.smp", "SMP Administrator", "admin", "local"),
    )
    conn.execute(
        """
        INSERT INTO subscriptions(user_id, status, trial_end)
        VALUES (?, 'trialing', date('now', '+14 days'))
        """,
        (cur.lastrowid,),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO discount_codes(code, description, percent_off, amount_off, active)
        VALUES ('WELCOME14', 'Example admin-provided launch discount', 10, 0, 1)
        """
    )
    conn.commit()
    conn.close()


def init_db(force=False):
    if DB.exists() and not force:
        return
    if DB.exists():
        DB.unlink()
    if not WORKBOOK.exists():
        create_empty_db()
        return
    reader = WorkbookReader(WORKBOOK)
    roster, _ = reader.cells("Student Roster")
    fee, _ = reader.cells("Fee Tracker")
    settings, _ = reader.cells("Settings")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            number INTEGER,
            student_name TEXT NOT NULL,
            parent_guardian TEXT,
            status TEXT NOT NULL DEFAULT 'C',
            enrol_date TEXT,
            subjects TEXT NOT NULL,
            rate_type TEXT,
            std_monthly_fee REAL NOT NULL DEFAULT 0,
            payment_method TEXT,
            phone TEXT,
            email TEXT,
            siblings TEXT,
            notes TEXT,
            last_modification TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            month_label TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            UNIQUE(student_id, month_label)
        );
        CREATE TABLE rates (
            id INTEGER PRIMARY KEY,
            subject TEXT NOT NULL,
            rate_type TEXT NOT NULL,
            monthly_fee REAL NOT NULL,
            description TEXT,
            UNIQUE(subject, rate_type)
        );
        CREATE TABLE app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'staff',
            auth_provider TEXT NOT NULL DEFAULT 'local',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'trialing',
            trial_start TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            trial_end TEXT,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            cancel_requested_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE discount_codes (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            description TEXT,
            percent_off REAL NOT NULL DEFAULT 0,
            amount_off REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE payer_aliases (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(student_id, alias)
        );
        CREATE TABLE payment_imports (
            id INTEGER PRIMARY KEY,
            file_name TEXT,
            source TEXT,
            imported_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE payment_import_rows (
            id INTEGER PRIMARY KEY,
            import_id INTEGER REFERENCES payment_imports(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
            transaction_date TEXT,
            description TEXT,
            amount REAL NOT NULL DEFAULT 0,
            source TEXT,
            month_label TEXT,
            match_score INTEGER NOT NULL DEFAULT 0,
            match_status TEXT NOT NULL DEFAULT 'review',
            notes TEXT,
            applied_at TEXT
        );
        """
    )

    row = 3
    while row < 500:
        name = str(cell(roster, "B", row)).strip()
        status = str(cell(roster, "D", row)).strip().upper()[:1]
        if not name or name.upper().startswith("TOTAL") or status not in {"C", "D"}:
            row += 1
            continue
        number = int(money(cell(roster, "A", row))) if cell(roster, "A", row) else None
        payload = (
            number,
            name,
            cell(roster, "C", row),
            status,
            excel_date(cell(roster, "E", row)),
            cell(roster, "F", row),
            cell(roster, "G", row),
            money(cell(roster, "H", row)),
            cell(roster, "I", row),
            cell(roster, "J", row),
            cell(roster, "K", row),
            cell(roster, "L", row),
            cell(roster, "M", row),
        )
        cur = conn.execute(
            """
            INSERT INTO students (
                number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
                std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (*payload, datetime.now().strftime("%Y-%m-%d: Created")),
        )
        student_id = cur.lastrowid
        fee_row = row + 1
        for index, month_label in enumerate(MONTHS, start=11):
            col = ""
            n = index
            while n:
                n, rem = divmod(n - 1, 26)
                col = chr(65 + rem) + col
            conn.execute(
                "INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,?)",
                (student_id, month_label, money(cell(fee, col, fee_row))),
            )
        row += 1

    for row in range(3, 12):
        subject = str(cell(settings, "A", row)).strip()
        rate_type = str(cell(settings, "B", row)).strip()
        if subject and rate_type:
            conn.execute(
                "INSERT OR REPLACE INTO rates(subject, rate_type, monthly_fee, description) VALUES (?,?,?,?)",
                (subject, rate_type, money(cell(settings, "C", row)), cell(settings, "D", row)),
            )

    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT INTO app_meta(key,value) VALUES (?,?)", (key, value))
    conn.execute("INSERT INTO app_meta(key,value) VALUES (?,?)", ("formula_manifest", json.dumps(FORMULA_MANIFEST)))
    cur = conn.execute(
        "INSERT INTO users(email, display_name, role, auth_provider) VALUES (?,?,?,?)",
        ("admin@local.smp", "SMP Administrator", "admin", "local"),
    )
    conn.execute(
        """
        INSERT INTO subscriptions(user_id, status, trial_end)
        VALUES (?, 'trialing', date('now', '+14 days'))
        """,
        (cur.lastrowid,),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO discount_codes(code, description, percent_off, amount_off, active)
        VALUES ('WELCOME14', 'Example admin-provided launch discount', 10, 0, 1)
        """
    )
    conn.commit()
    conn.close()


def ensure_meta_defaults():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'staff',
                auth_provider TEXT NOT NULL DEFAULT 'local',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'trialing',
                trial_start TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                trial_end TEXT,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                cancel_requested_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS discount_codes (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                description TEXT,
                percent_off REAL NOT NULL DEFAULT 0,
                amount_off REAL NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payer_aliases (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, alias)
            );
            CREATE TABLE IF NOT EXISTS payment_imports (
                id INTEGER PRIMARY KEY,
                file_name TEXT,
                source TEXT,
                imported_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payment_import_rows (
                id INTEGER PRIMARY KEY,
                import_id INTEGER REFERENCES payment_imports(id) ON DELETE CASCADE,
                student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
                transaction_date TEXT,
                description TEXT,
                amount REAL NOT NULL DEFAULT 0,
                source TEXT,
                month_label TEXT,
                match_score INTEGER NOT NULL DEFAULT 0,
                match_status TEXT NOT NULL DEFAULT 'review',
                notes TEXT,
                applied_at TEXT
            );
            """
        )
        student_columns = [row["name"] for row in conn.execute("PRAGMA table_info(students)").fetchall()]
        if "last_modification" not in student_columns:
            conn.execute("ALTER TABLE students ADD COLUMN last_modification TEXT")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES (?,?)", (key, value))
        conn.execute(
            """
            UPDATE app_meta
            SET value=?
            WHERE key='institution_name' AND value='Learning Centre'
            """,
            (DEFAULT_SETTINGS["institution_name"],),
        )
        conn.execute(
            """
            UPDATE app_meta
            SET value=?
            WHERE key='institution_details' AND value='Student roster, fee tracking, and monthly collection dashboard.'
            """,
            (DEFAULT_SETTINGS["institution_details"],),
        )
        conn.execute("INSERT OR IGNORE INTO app_meta(key,value) VALUES (?,?)", ("formula_manifest", json.dumps(FORMULA_MANIFEST)))
        cur = conn.execute(
            "INSERT OR IGNORE INTO users(email, display_name, role, auth_provider) VALUES (?,?,?,?)",
            ("admin@local.smp", "SMP Administrator", "admin", "local"),
        )
        admin = conn.execute("SELECT id FROM users WHERE email='admin@local.smp'").fetchone()
        if admin:
            existing = conn.execute("SELECT id FROM subscriptions WHERE user_id=? ORDER BY id", (admin["id"],)).fetchall()
            if not existing:
                conn.execute(
                    """
                    INSERT INTO subscriptions(user_id, status, trial_end)
                    VALUES (?, 'trialing', date('now', '+14 days'))
                    """,
                    (admin["id"],),
                )
            elif len(existing) > 1:
                keep = existing[0]["id"]
                conn.execute("DELETE FROM subscriptions WHERE user_id=? AND id<>?", (admin["id"], keep))
        conn.execute(
            """
            INSERT OR IGNORE INTO discount_codes(code, description, percent_off, amount_off, active)
            VALUES ('WELCOME14', 'Example admin-provided launch discount', 10, 0, 1)
            """
        )
        conn.commit()


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rowdict(row):
    return dict(row)


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM app_meta").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    for key, value in DEFAULT_SETTINGS.items():
        values.setdefault(key, value)
    return values


def configured_subjects(settings=None):
    settings = settings or {}
    raw = settings.get("subjects_offered", DEFAULT_SETTINGS["subjects_offered"])
    subjects = []
    for line in re.split(r"[\n,]+", raw):
        subject = line.strip()
        if subject and subject.lower() not in [s.lower() for s in subjects]:
            subjects.append(subject)
    return subjects or ["Math", "English"]


def subject_list(subjects):
    if isinstance(subjects, list):
        values = subjects
    else:
        raw = str(subjects or "").strip()
        if raw.lower() == "both":
            values = ["Math", "English"]
        else:
            values = re.split(r"[,;/|]+", raw)
    clean = []
    for value in values:
        subject = str(value).strip()
        if subject and subject.lower() not in [s.lower() for s in clean]:
            clean.append(subject)
    return clean


def subjects_text(subjects):
    return ", ".join(subject_list(subjects))


def list_backups():
    if not BACKUP_DIR.exists():
        return []
    backups = []
    for item in sorted(BACKUP_DIR.glob("*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = item.stat()
        backups.append(
            {
                "name": item.name,
                "path": str(item),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return backups


def restore_backup(filename):
    candidate = (BACKUP_DIR / filename).resolve()
    backup_root = BACKUP_DIR.resolve()
    if backup_root not in candidate.parents or candidate.suffix.lower() != ".sqlite3" or not candidate.exists():
        raise ValueError("Backup file was not found in C:/Back/Day")
    shutil.copy2(candidate, DB)
    ensure_meta_defaults()


def normalize_student(data):
    data = data or {}
    required = ["student_name", "status", "subjects"]
    missing = [name for name in required if not str(data.get(name, "")).strip()]
    if missing:
        raise ValueError("Missing required fields: " + ", ".join(missing))
    status = str(data.get("status", "C")).strip().upper()[:1]
    if status not in {"C", "D"}:
        raise ValueError("Status must be C or D")
    subjects = subjects_text(data.get("subjects", ""))
    if not subjects:
        raise ValueError("At least one subject is required")
    rate_type = str(data.get("rate_type") or "Regular").strip()
    fee = money(data.get("std_monthly_fee"))
    if not fee:
        with db() as conn:
            fee = 0
            for subject in subject_list(subjects):
                rate = conn.execute(
                    "SELECT monthly_fee FROM rates WHERE lower(subject)=lower(?) AND rate_type=?",
                    (subject, rate_type),
                ).fetchone()
                fee += float(rate["monthly_fee"]) if rate else 0
    return {
        "student_name": str(data.get("student_name", "")).strip(),
        "parent_guardian": str(data.get("parent_guardian", "")).strip(),
        "status": status,
        "enrol_date": str(data.get("enrol_date", "")).strip(),
        "subjects": subjects,
        "rate_type": rate_type,
        "std_monthly_fee": fee,
        "payment_method": str(data.get("payment_method", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "siblings": str(data.get("siblings", "")).strip(),
        "notes": str(data.get("notes", "")).strip(),
    }


STUDENT_FIELD_LABELS = {
    "student_name": "Student Name",
    "parent_guardian": "Parent / Guardian",
    "status": "Status",
    "enrol_date": "Enrol Date",
    "subjects": "Subjects",
    "rate_type": "Rate Type",
    "std_monthly_fee": "STD Fee",
    "payment_method": "Payment Method",
    "phone": "Phone",
    "email": "Email",
    "siblings": "Siblings",
    "notes": "Notes",
}


def student_modification_note(old, new):
    if not old:
        return datetime.now().strftime("%Y-%m-%d: Created")
    changed = []
    for key, label in STUDENT_FIELD_LABELS.items():
        old_value = old[key] if key in old.keys() else ""
        new_value = new.get(key, "")
        if key == "std_monthly_fee":
            different = abs(float(old_value or 0) - float(new_value or 0)) > 0.01
        else:
            different = str(old_value or "").strip() != str(new_value or "").strip()
        if different:
            changed.append(label)
    if not changed:
        return old["last_modification"] if "last_modification" in old.keys() else ""
    return f"{datetime.now().strftime('%Y-%m-%d')}: " + ", ".join(changed)


def subject_units(subjects):
    return len(subject_list(subjects))


def month_to_date(month_label):
    try:
        return datetime.strptime(month_label, "%b-%y")
    except ValueError:
        return datetime(1900, 1, 1)


def transaction_month_label(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y", "%b %d %Y", "%d %b %Y"]:
        try:
            parsed = datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt)
            label = parsed.strftime("%b-%y")
            return label if label in MONTHS else ""
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        label = parsed.strftime("%b-%y")
        return label if label in MONTHS else ""
    except ValueError:
        return ""


def previous_month_label(month_label):
    if month_label not in MONTHS:
        return ""
    index = MONTHS.index(month_label)
    return MONTHS[index - 1] if index > 0 else ""


def clean_match_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def meaningful_tokens(value):
    ignored = {"and", "the", "for", "fee", "fees", "payment", "transfer", "etransfer", "bank", "card", "from"}
    return [token for token in clean_match_text(value).split() if len(token) >= 3 and token not in ignored]


def score_payment_match(row, student, aliases, payments):
    description = clean_match_text(f"{row.get('description', '')} {row.get('source', '')}")
    amount = money(row.get("amount"))
    month_label = transaction_month_label(row.get("date") or row.get("transaction_date"))
    score = 0
    reasons = []

    parent = student.get("parent_guardian", "")
    parent_text = clean_match_text(parent)
    if parent_text and parent_text in description:
        score += 38
        reasons.append("parent/guardian full name found")
    else:
        matched_tokens = [token for token in meaningful_tokens(parent) if token in description]
        if matched_tokens:
            score += min(32, len(matched_tokens) * 14)
            reasons.append("parent/guardian name token match")

    alias_hit = ""
    for alias in aliases.get(student["id"], []):
        alias_text = clean_match_text(alias)
        if alias_text and alias_text in description:
            alias_hit = alias
            score += 45
            reasons.append(f"saved payer alias: {alias}")
            break

    student_tokens = [token for token in meaningful_tokens(student.get("student_name", "")) if token in description]
    if student_tokens:
        score += min(22, len(student_tokens) * 12)
        reasons.append("student name token match")

    expected = float(student.get("std_monthly_fee") or 0)
    if expected and abs(amount - expected) <= 0.01:
        score += 25
        reasons.append("amount matches standard monthly fee")
    elif expected and 0 < abs(amount - expected) <= 5:
        score += 12
        reasons.append("amount is close to standard fee")

    prev_month = previous_month_label(month_label)
    prev_paid = float(payments.get(student["id"], {}).get(prev_month, 0) or 0) if prev_month else 0
    enrol_date = str(student.get("enrol_date") or "")
    month_date = month_to_date(month_label)
    is_new_enrolment = bool(enrol_date and month_label and enrol_date.startswith(month_date.strftime("%Y-%m")))
    if prev_month and prev_paid:
        score += 14
        reasons.append("same student paid last month")
        if abs(prev_paid - amount) <= 0.01:
            score += 10
            reasons.append("amount matches last month payment")
    elif is_new_enrolment:
        score += 8
        reasons.append("new enrolment: previous month exception")
    elif prev_month:
        reasons.append("no previous month payment found")

    if parent_text and SequenceMatcher(None, parent_text, description).ratio() >= 0.55:
        score += 8
        reasons.append("description is similar to guardian name")

    return {
        "student_id": student["id"],
        "student_name": student["student_name"],
        "parent_guardian": student.get("parent_guardian", ""),
        "month_label": month_label,
        "score": min(score, 100),
        "confidence": "high" if score >= 75 else "medium" if score >= 50 else "low",
        "reasons": reasons,
        "alias": alias_hit,
        "expected_fee": expected,
        "previous_month": prev_month,
        "previous_paid": prev_paid,
    }


def preview_reconciliation(rows):
    with db() as conn:
        students = [rowdict(row) for row in conn.execute("SELECT * FROM students WHERE upper(status)='C' ORDER BY student_name")]
        payments = get_payments(conn)
        alias_rows = conn.execute("SELECT student_id, alias FROM payer_aliases").fetchall()
    aliases = {}
    for row in alias_rows:
        aliases.setdefault(row["student_id"], []).append(row["alias"])

    previews = []
    for index, row in enumerate(rows, start=1):
        normalized = {
            "row_number": index,
            "date": str(row.get("date") or row.get("transaction_date") or "").strip(),
            "description": str(row.get("description") or row.get("memo") or row.get("name") or "").strip(),
            "amount": money(row.get("amount") or row.get("credit") or row.get("deposit")),
            "source": str(row.get("source") or row.get("account") or "").strip(),
        }
        matches = sorted(
            [score_payment_match(normalized, student, aliases, payments) for student in students],
            key=lambda match: match["score"],
            reverse=True,
        )[:5]
        best = matches[0] if matches else None
        previews.append(
            {
                **normalized,
                "month_label": best["month_label"] if best else transaction_month_label(normalized["date"]),
                "best_match": best,
                "candidates": matches,
                "suggestion": "auto-fill" if best and best["confidence"] == "high" else "review",
            }
        )
    return previews


def reconciliation_summary(conn):
    rows = conn.execute(
        """
        SELECT match_status, COUNT(*) AS count, COALESCE(SUM(amount),0) AS total
        FROM payment_import_rows
        GROUP BY match_status
        """
    ).fetchall()
    return [rowdict(row) for row in rows]


def csv_response(rows, headers):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({header: row.get(header, "") for header in headers})
    return output.getvalue().encode("utf-8-sig")


def roster_export_rows(conn):
    headers = [
        "number", "student_name", "parent_guardian", "status", "enrol_date", "subjects", "last_modification",
        "rate_type", "std_monthly_fee", "payment_method", "phone", "email", "siblings", "notes",
    ]
    return headers, [{header: row.get(header, "") for header in headers} for row in get_students(conn)]


def fee_export_rows(conn):
    headers = [
        "number", "student_name", "parent_guardian", "status", "enrol_date", "subjects", "rate_type",
        "std_monthly_fee", "payment_method", "subject_units", *MONTHS, "total_paid", "balance",
    ]
    rows = []
    for row in fee_tracker(conn):
        item = {header: row.get(header, "") for header in headers}
        for month in MONTHS:
            item[month] = row["months"].get(month, 0)
        rows.append(item)
    return headers, rows


def student_lookup_key(student_name, parent_guardian):
    return f"{clean_match_text(student_name)}|{clean_match_text(parent_guardian)}"


def preview_fee_import(rows):
    with db() as conn:
        students = get_students(conn)
        lookup = {student_lookup_key(row["student_name"], row["parent_guardian"]): row for row in students}
    preview = []
    for index, row in enumerate(rows, start=1):
        name = str(row.get("student_name") or row.get("Student Name") or "").strip()
        parent = str(row.get("parent_guardian") or row.get("Parent / Guardian") or "").strip()
        key = student_lookup_key(name, parent)
        student = lookup.get(key)
        month_values = {month: money(row.get(month)) for month in MONTHS if str(row.get(month, "")).strip()}
        preview.append(
            {
                "row_number": index,
                "student_name": name,
                "parent_guardian": parent,
                "student_id": student["id"] if student else None,
                "matched_student": student["student_name"] if student else "",
                "matched_parent": student["parent_guardian"] if student else "",
                "matched": bool(student),
                "month_count": len(month_values),
                "total_amount": sum(month_values.values()),
                "months": month_values,
            }
        )
    return preview


def apply_fee_import(rows):
    preview = preview_fee_import(rows)
    applied = 0
    skipped = 0
    with db() as conn:
        for item in preview:
            if not item["student_id"]:
                skipped += 1
                continue
            for month, amount in item["months"].items():
                conn.execute(
                    """
                    INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,?)
                    ON CONFLICT(student_id, month_label) DO UPDATE SET amount=excluded.amount
                    """,
                    (item["student_id"], month, amount),
                )
                applied += 1
        conn.commit()
    return {"applied": applied, "skipped": skipped, "rows": preview}


def recent_months(current_month, count=13):
    if current_month not in MONTHS:
        current_month = "May-26"
    end = MONTHS.index(current_month)
    start = max(0, end - count + 1)
    months = MONTHS[start : end + 1]
    if len(months) < count:
        months = MONTHS[:count]
    return months[-count:]


def get_students(conn):
    return [rowdict(row) for row in conn.execute("SELECT * FROM students ORDER BY number, student_name")]


def get_payments(conn):
    rows = conn.execute("SELECT student_id, month_label, amount FROM payments").fetchall()
    by_student = {}
    for row in rows:
        by_student.setdefault(row["student_id"], {})[row["month_label"]] = float(row["amount"] or 0)
    return by_student


def fee_tracker(conn):
    payments = get_payments(conn)
    rows = []
    for student in get_students(conn):
        month_values = {month: payments.get(student["id"], {}).get(month, 0) for month in MONTHS}
        total_paid = sum(month_values.values())
        balance = max(0, float(student["std_monthly_fee"] or 0) - total_paid) if student["status"].lower() == "c" else 0
        rows.append(
            {
                **student,
                "subject_units": subject_units(student["subjects"]),
                "subject_list": subject_list(student["subjects"]),
                "subjects_display": subjects_text(student["subjects"]),
                "months": month_values,
                "total_paid": total_paid,
                "balance": balance,
            }
        )
    return rows


def dashboard(conn, current_month="May-26"):
    rows = fee_tracker(conn)
    active = [row for row in rows if row["status"].upper() == "C" and row["subjects"]]
    discontinued = [row for row in rows if row["status"].upper() == "D"]
    last_13 = recent_months(current_month, 13)
    monthly_totals = []
    for month in last_13:
        amounts = [row["months"].get(month, 0) for row in active]
        monthly_totals.append({"month": month, "total": sum(amounts), "count": sum(1 for amount in amounts if amount > 0)})
    enrolment_totals = []
    for month in last_13:
        month_key = month_to_date(month).strftime("%Y-%m")
        count = sum(1 for row in rows if str(row.get("enrol_date", "")).startswith(month_key))
        enrolment_totals.append({"month": month, "count": count})
    by_method = {}
    for row in active:
        method = row["payment_method"] or "Unspecified"
        by_method[method] = by_method.get(method, 0) + row["months"].get(current_month, 0)
    subject_breakdown = {}
    for row in active:
        for subject in subject_list(row["subjects"]):
            subject_breakdown[subject] = subject_breakdown.get(subject, 0) + 1
    return {
        "current_month": current_month,
        "active_students": len(active),
        "discontinued_students": len(discontinued),
        "may_2026_revenue": sum(row["months"].get("May-26", 0) for row in active),
        "annual_projected_revenue": sum(float(row["std_monthly_fee"] or 0) for row in active) * 12,
        "math_only": sum(1 for row in active if row["subjects"] == "Math"),
        "english_only": sum(1 for row in active if row["subjects"] == "English"),
        "both_subjects": sum(1 for row in active if row["subjects"].lower() == "both" or len(subject_list(row["subjects"])) > 1),
        "total_enrolment": sum(subject_units(row["subjects"]) for row in active),
        "subject_breakdown": subject_breakdown,
        "outstanding_balance": sum(row["balance"] for row in rows),
        "by_payment_method": by_method,
        "monthly_totals": monthly_totals,
        "enrolment_totals": enrolment_totals,
    }


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_csv(self, filename, rows, headers):
        data = csv_response(rows, headers)
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/bootstrap":
            with db() as conn:
                settings = get_settings(conn)
                self.send_json(
                    {
                        "students": get_students(conn),
                        "fee_tracker": fee_tracker(conn),
                        "dashboard": dashboard(conn, settings.get("current_month", "May-26")),
                        "rates": [rowdict(row) for row in conn.execute("SELECT * FROM rates ORDER BY subject, rate_type")],
                        "months": MONTHS,
                        "formula_manifest": FORMULA_MANIFEST,
                        "settings": settings,
                        "users": [rowdict(row) for row in conn.execute("SELECT id, email, display_name, role, auth_provider, created_at FROM users ORDER BY role, email")],
                        "subscriptions": [rowdict(row) for row in conn.execute("SELECT * FROM subscriptions ORDER BY id DESC")],
                        "discount_codes": [rowdict(row) for row in conn.execute("SELECT * FROM discount_codes ORDER BY active DESC, code")],
                        "backups": list_backups(),
                        "reconciliation": reconciliation_summary(conn),
                        "payer_aliases": [rowdict(row) for row in conn.execute("SELECT * FROM payer_aliases ORDER BY alias")],
                    }
                )
            return
        if parsed.path == "/api/export":
            with db() as conn:
                self.send_json({"students": get_students(conn), "fee_tracker": fee_tracker(conn), "dashboard": dashboard(conn)})
            return
        if parsed.path == "/api/export/roster.csv":
            with db() as conn:
                headers, rows = roster_export_rows(conn)
                self.send_csv("smp_student_roster.csv", rows, headers)
            return
        if parsed.path == "/api/export/fee-tracker.csv":
            with db() as conn:
                headers, rows = fee_export_rows(conn)
                self.send_csv("smp_fee_tracker.csv", rows, headers)
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/students":
                student = normalize_student(self.read_json())
                with db() as conn:
                    next_number = conn.execute("SELECT COALESCE(MAX(number),0)+1 AS n FROM students").fetchone()["n"]
                    cur = conn.execute(
                        """
                        INSERT INTO students (
                            number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
                            std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            next_number,
                            student["student_name"],
                            student["parent_guardian"],
                            student["status"],
                            student["enrol_date"],
                            student["subjects"],
                            student["rate_type"],
                            student["std_monthly_fee"],
                            student["payment_method"],
                            student["phone"],
                            student["email"],
                            student["siblings"],
                            student["notes"],
                            datetime.now().strftime("%Y-%m-%d: Created"),
                        ),
                    )
                    for month in MONTHS:
                        conn.execute(
                            "INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,0)",
                            (cur.lastrowid, month),
                        )
                    conn.commit()
                    self.send_json({"ok": True, "id": cur.lastrowid})
                return
            if parsed.path == "/api/batch":
                rows = self.read_json().get("rows", [])
                saved = []
                for item in rows:
                    if str(item.get("student_name", "")).strip():
                        saved.append(normalize_student(item))
                with db() as conn:
                    next_number = conn.execute("SELECT COALESCE(MAX(number),0)+1 AS n FROM students").fetchone()["n"]
                    for student in saved:
                        cur = conn.execute(
                            """
                            INSERT INTO students (
                                number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
                                std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                next_number,
                                student["student_name"],
                                student["parent_guardian"],
                                student["status"],
                                student["enrol_date"],
                                student["subjects"],
                                student["rate_type"],
                                student["std_monthly_fee"],
                                student["payment_method"],
                                student["phone"],
                                student["email"],
                                student["siblings"],
                                student["notes"],
                                datetime.now().strftime("%Y-%m-%d: Created"),
                            ),
                        )
                        for month in MONTHS:
                            conn.execute("INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,0)", (cur.lastrowid, month))
                        next_number += 1
                    conn.commit()
                self.send_json({"ok": True, "saved": len(saved)})
                return
            if parsed.path == "/api/settings":
                payload = self.read_json()
                with db() as conn:
                    for key in DEFAULT_SETTINGS:
                        if key in payload:
                            conn.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES (?,?)", (key, str(payload.get(key, ""))))
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/rates":
                payload = self.read_json()
                subject = str(payload.get("subject", "")).strip()
                rate_type = str(payload.get("rate_type", "")).strip()
                if not subject or not rate_type:
                    raise ValueError("Subject and rate type are required")
                with db() as conn:
                    cur = conn.execute(
                        "INSERT INTO rates(subject, rate_type, monthly_fee, description) VALUES (?,?,?,?)",
                        (subject, rate_type, money(payload.get("monthly_fee")), str(payload.get("description", "")).strip()),
                    )
                    conn.commit()
                self.send_json({"ok": True, "id": cur.lastrowid})
                return
            if parsed.path == "/api/discounts":
                payload = self.read_json()
                code = str(payload.get("code", "")).strip().upper()
                if not code:
                    raise ValueError("Discount code is required")
                with db() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO discount_codes(code, description, percent_off, amount_off, active)
                        VALUES (?,?,?,?,?)
                        """,
                        (
                            code,
                            str(payload.get("description", "")).strip(),
                            money(payload.get("percent_off")),
                            money(payload.get("amount_off")),
                            1 if str(payload.get("active", "1")) in {"1", "true", "on", "yes"} else 0,
                        ),
                    )
                    conn.commit()
                    self.send_json({"ok": True, "id": cur.lastrowid})
                return
            if parsed.path == "/api/restore":
                payload = self.read_json()
                if payload.get("confirm") != "RESTORE":
                    raise ValueError("Type RESTORE to confirm database recovery")
                restore_backup(str(payload.get("name", "")))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/reconciliation/preview":
                payload = self.read_json()
                rows = payload.get("rows", [])
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Upload at least one payment transaction row")
                self.send_json({"ok": True, "rows": preview_reconciliation(rows[:500])})
                return
            if parsed.path == "/api/reconciliation/apply":
                payload = self.read_json()
                student_id = int(payload.get("student_id") or 0)
                month_label = str(payload.get("month_label") or "").strip()
                amount = money(payload.get("amount"))
                description = str(payload.get("description") or "").strip()
                source = str(payload.get("source") or "").strip()
                if not student_id or month_label not in MONTHS:
                    raise ValueError("Student and month are required before applying a match")
                with db() as conn:
                    student = conn.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
                    if not student:
                        raise ValueError("Student was not found")
                    conn.execute(
                        """
                        INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,?)
                        ON CONFLICT(student_id, month_label) DO UPDATE SET amount=excluded.amount
                        """,
                        (student_id, month_label, amount),
                    )
                    if description:
                        alias = description[:120]
                        conn.execute(
                            "INSERT OR IGNORE INTO payer_aliases(student_id, alias, source) VALUES (?,?,?)",
                            (student_id, alias, source),
                        )
                    cur = conn.execute(
                        "INSERT INTO payment_imports(file_name, source, imported_by) VALUES (?,?,?)",
                        (str(payload.get("file_name") or "manual review"), source, "admin"),
                    )
                    conn.execute(
                        """
                        INSERT INTO payment_import_rows(
                            import_id, student_id, transaction_date, description, amount, source,
                            month_label, match_score, match_status, notes, applied_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                        """,
                        (
                            cur.lastrowid,
                            student_id,
                            str(payload.get("date") or "").strip(),
                            description,
                            amount,
                            source,
                            month_label,
                            int(payload.get("score") or 0),
                            "approved",
                            str(payload.get("notes") or "").strip(),
                        ),
                    )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/fee-import/preview":
                payload = self.read_json()
                rows = payload.get("rows", [])
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Upload at least one fee tracker CSV row")
                self.send_json({"ok": True, "rows": preview_fee_import(rows[:1000])})
                return
            if parsed.path == "/api/fee-import/apply":
                payload = self.read_json()
                rows = payload.get("rows", [])
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Upload at least one fee tracker CSV row")
                self.send_json({"ok": True, **apply_fee_import(rows[:1000])})
                return
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_PUT(self):
        parsed = urlparse(self.path)
        match = re.match(r"^/api/students/(\d+)$", parsed.path)
        rate_match = re.match(r"^/api/rates/(\d+)$", parsed.path)
        discount_match = re.match(r"^/api/discounts/(\d+)$", parsed.path)
        payment_match = re.match(r"^/api/payments/(\d+)/([^/]+)$", parsed.path)
        try:
            if payment_match:
                student_id = int(payment_match.group(1))
                month_label = payment_match.group(2)
                if month_label not in MONTHS:
                    raise ValueError("Unknown month")
                payload = self.read_json()
                with db() as conn:
                    conn.execute(
                        """
                        INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,?)
                        ON CONFLICT(student_id, month_label) DO UPDATE SET amount=excluded.amount
                        """,
                        (student_id, month_label, money(payload.get("amount"))),
                    )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if rate_match:
                payload = self.read_json()
                subject = str(payload.get("subject", "")).strip()
                rate_type = str(payload.get("rate_type", "")).strip()
                if not subject or not rate_type:
                    raise ValueError("Subject and rate type are required")
                with db() as conn:
                    conn.execute(
                        "UPDATE rates SET subject=?, rate_type=?, monthly_fee=?, description=? WHERE id=?",
                        (subject, rate_type, money(payload.get("monthly_fee")), str(payload.get("description", "")).strip(), int(rate_match.group(1))),
                    )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if discount_match:
                payload = self.read_json()
                code = str(payload.get("code", "")).strip().upper()
                if not code:
                    raise ValueError("Discount code is required")
                with db() as conn:
                    conn.execute(
                        """
                        UPDATE discount_codes
                        SET code=?, description=?, percent_off=?, amount_off=?, active=?
                        WHERE id=?
                        """,
                        (
                            code,
                            str(payload.get("description", "")).strip(),
                            money(payload.get("percent_off")),
                            money(payload.get("amount_off")),
                            1 if str(payload.get("active", "1")) in {"1", "true", "on", "yes"} else 0,
                            int(discount_match.group(1)),
                        ),
                    )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if not match:
                self.send_json({"ok": False, "error": "Not found"}, 404)
                return
            student = normalize_student(self.read_json())
            with db() as conn:
                old_student = conn.execute("SELECT * FROM students WHERE id=?", (int(match.group(1)),)).fetchone()
                modification_note = student_modification_note(old_student, student)
                conn.execute(
                    """
                    UPDATE students SET
                        student_name=?, parent_guardian=?, status=?, enrol_date=?, subjects=?, rate_type=?,
                        std_monthly_fee=?, payment_method=?, phone=?, email=?, siblings=?, notes=?, last_modification=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        student["student_name"],
                        student["parent_guardian"],
                        student["status"],
                        student["enrol_date"],
                        student["subjects"],
                        student["rate_type"],
                        student["std_monthly_fee"],
                        student["payment_method"],
                        student["phone"],
                        student["email"],
                        student["siblings"],
                        student["notes"],
                        modification_note,
                        int(match.group(1)),
                    ),
                )
                conn.commit()
            self.send_json({"ok": True})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        match = re.match(r"^/api/students/(\d+)$", parsed.path)
        rate_match = re.match(r"^/api/rates/(\d+)$", parsed.path)
        discount_match = re.match(r"^/api/discounts/(\d+)$", parsed.path)
        if rate_match:
            with db() as conn:
                conn.execute("DELETE FROM rates WHERE id=?", (int(rate_match.group(1)),))
                conn.commit()
            self.send_json({"ok": True})
            return
        if discount_match:
            with db() as conn:
                conn.execute("DELETE FROM discount_codes WHERE id=?", (int(discount_match.group(1)),))
                conn.commit()
            self.send_json({"ok": True})
            return
        if not match:
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        with db() as conn:
            conn.execute("DELETE FROM students WHERE id=?", (int(match.group(1)),))
            conn.commit()
        self.send_json({"ok": True})


if __name__ == "__main__":
    init_db(force="--reseed" in sys.argv)
    ensure_meta_defaults()
    port = 8765
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"SMP tracking site running at http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
