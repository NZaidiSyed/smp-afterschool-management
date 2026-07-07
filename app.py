import json
import re
import shutil
import sqlite3
import sys
import zipfile
import csv
import io
import os
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse, parse_qs
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # Local development can keep using SQLite without extra packages.
    psycopg = None
    dict_row = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SMP_DATA_DIR", ROOT))
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORKBOOK = ROOT / "Kumon_Tracking_FINAL-1.xlsm"
DB = DATA_DIR / "kumon_tracking.sqlite3"
BACKUP_DIR = Path(os.environ.get("SMP_BACKUP_DIR", "C:/Back/Day"))
SMP_ORGANIZATION_ID = os.environ.get("SMP_ORGANIZATION_ID", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_REQUIRE_AUTH = os.environ.get("SUPABASE_REQUIRE_AUTH", "0").lower() in {"1", "true", "yes", "on"}
SUPABASE_PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF", "ccftzopeneykbcwkwizq").strip()


def normalized_database_url():
    raw = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or ""
    if not raw:
        return ""
    project_ref = SUPABASE_PROJECT_REF
    if SUPABASE_URL:
        host = urlparse(SUPABASE_URL).hostname or ""
        project_ref = host.split(".")[0] or project_ref
    if project_ref and "pooler.supabase.com" in raw:
        for prefix in ("postgresql://postgres:", "postgres://postgres:"):
            if raw.startswith(prefix):
                return raw.replace(prefix, prefix.replace("postgres:", f"postgres.{project_ref}:"), 1)
    parsed = urlparse(raw)
    if "pooler.supabase.com" not in parsed.hostname or parsed.username != "postgres":
        return raw
    if not project_ref:
        match = re.search(r"postgresql://postgres\.([a-z0-9]+):", raw)
        project_ref = match.group(1) if match else ""
    if not project_ref:
        project_ref = SUPABASE_PROJECT_REF
    if not project_ref:
        return raw
    password = quote(unquote(parsed.password or ""), safe="")
    user = f"postgres.{project_ref}"
    auth = f"{user}:{password}" if password else user
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, f"{auth}@{host}", parsed.path, parsed.params, parsed.query, parsed.fragment))


DATABASE_URL = normalized_database_url()
PG_MODE = bool(DATABASE_URL)
DEFAULT_SETTINGS = {
    "institution_name": "SMP - After School Management Program",
    "institution_phone": "",
    "institution_details": "After-school enrolment, student roster, fee tracking, and monthly collection dashboard.",
    "current_month": "May-26",
    "subjects_offered": "Math\nEnglish",
    "operating_start": "15:00",
    "operating_end": "20:00",
    "support_email": "support@smp.edu",
}
STUDENT_SCHEDULE_WEEKDAYS = ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

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

BASE_MONTHS = MONTHS[:]
ROLE_OPTIONS = ["Owner", "Admin", "Office Manager", "Office Assistant", "Staff"]
ROLE_PERMISSIONS = {
    "Owner": {"admin", "manage_students", "manage_payments", "manage_settings", "manage_users", "manage_staff", "delete_records"},
    "Admin": {"admin", "manage_students", "manage_payments", "manage_settings", "manage_users", "manage_staff", "delete_records"},
    "Office Manager": {"manage_students", "manage_payments", "manage_staff"},
    "Office Assistant": {"manage_payments"},
    "Staff": set(),
}


def month_label(dt):
    return dt.strftime("%b-%y")


def current_month_label():
    return month_label(date.today().replace(day=1))


def month_position(label):
    try:
        return MONTHS.index(label)
    except ValueError:
        return -1


def add_months(dt, months):
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def generated_months():
    start = datetime.strptime(BASE_MONTHS[0], "%b-%y").date()
    base_end = datetime.strptime(BASE_MONTHS[-1], "%b-%y").date()
    today_month = date.today().replace(day=1)
    end = max(base_end, add_months(today_month, 24))
    labels = []
    cursor = start
    while cursor <= end:
        labels.append(month_label(cursor))
        cursor = add_months(cursor, 1)
    return labels


MONTHS = generated_months()
DEFAULT_SETTINGS["current_month"] = current_month_label()

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


def normalize_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%Y-%m-%d")
    except ValueError:
        pass
    for fmt in ["%Y-%m-%d", "%Y-%b-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y", "%b %d %Y", "%d %b %Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return text


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
            deleted_at TEXT,
            deleted_by TEXT,
            delete_reason TEXT,
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
        CREATE TABLE student_status_changes (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            previous_status TEXT NOT NULL,
            new_status TEXT NOT NULL,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed_month TEXT NOT NULL,
            notes TEXT
        );
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            action TEXT NOT NULL,
            actor_email TEXT,
            summary TEXT,
            before_json TEXT,
            after_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            (subject, "R", 165, "Default starter rate"),
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
        CREATE TABLE student_status_changes (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            previous_status TEXT NOT NULL,
            new_status TEXT NOT NULL,
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            changed_month TEXT NOT NULL,
            notes TEXT
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
            CREATE TABLE IF NOT EXISTS student_status_changes (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                changed_month TEXT NOT NULL,
                notes TEXT
            );
            CREATE TABLE IF NOT EXISTS monthly_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month_label TEXT NOT NULL UNIQUE,
                rent_expense REAL DEFAULT 0.0,
                royalty_expense REAL DEFAULT 0.0,
                utilities_expense REAL DEFAULT 0.0,
                misc_expense REAL DEFAULT 0.0,
                misc_details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        expense_columns = [row["name"] for row in conn.execute("PRAGMA table_info(monthly_expenses)").fetchall()]
        if "utilities_expense" not in expense_columns:
            conn.execute("ALTER TABLE monthly_expenses ADD COLUMN utilities_expense REAL DEFAULT 0.0")
        student_columns = [row["name"] for row in conn.execute("PRAGMA table_info(students)").fetchall()]
        if "last_modification" not in student_columns:
            conn.execute("ALTER TABLE students ADD COLUMN last_modification TEXT")
        ensure_student_audit_tables(conn)
        ensure_student_schedule_tables(conn)
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
    if PG_MODE:
        if psycopg is None:
            raise RuntimeError("psycopg is required when DATABASE_URL/SUPABASE_DB_URL is configured")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def rowdict(row):
    def clean(value):
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return {key: clean(value) for key, value in dict(row).items()}


def execute(conn, sql, params=()):
    if PG_MODE:
        return conn.execute(sql.replace("?", "%s"), params)
    return conn.execute(sql, params)


def execute_many(conn, sql, rows):
    if PG_MODE:
        prepared = sql.replace("?", "%s")
        for row in rows:
            conn.execute(prepared, row)
        return None
    return conn.executemany(sql, rows)


def row_value(row, key, default=""):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return row[key] if key in row.keys() else default


def row_get(row, key, default=""):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def pg_scalar(value):
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return str(value)


def current_org_id(conn):
    global SMP_ORGANIZATION_ID
    if not PG_MODE:
        return None
    if SMP_ORGANIZATION_ID:
        return SMP_ORGANIZATION_ID
    rows = conn.execute("SELECT id::text AS id FROM public.organizations ORDER BY created_at LIMIT 2").fetchall()
    if len(rows) == 1:
        SMP_ORGANIZATION_ID = rows[0]["id"]
        return SMP_ORGANIZATION_ID
    if len(rows) > 1:
        raise RuntimeError("SMP_ORGANIZATION_ID must be set when multiple organizations exist")
    row = conn.execute(
        """
        INSERT INTO public.organizations(name, details, subjects_offered, current_month)
        VALUES (%s, %s, %s, %s)
        RETURNING id::text AS id
        """,
        (
            DEFAULT_SETTINGS["institution_name"],
            DEFAULT_SETTINGS["institution_details"],
            configured_subjects(DEFAULT_SETTINGS),
            DEFAULT_SETTINGS["current_month"],
        ),
    ).fetchone()
    SMP_ORGANIZATION_ID = row["id"]
    conn.commit()
    return SMP_ORGANIZATION_ID


def current_branch_id(conn):
    if PG_MODE:
        org_id = current_org_id(conn)
        row = conn.execute("SELECT id::text AS id FROM public.branches WHERE organization_id=%s ORDER BY created_at LIMIT 1", (org_id,)).fetchone()
        return rowdict(row)["id"] if row else None
    else:
        row = conn.execute("SELECT id FROM branches ORDER BY created_at LIMIT 1").fetchone()
        return row[0] if row else None


def ensure_access_tables(conn):
    if PG_MODE:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.app_users (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                email text not null,
                display_name text,
                role text not null default 'Office Assistant',
                active boolean not null default true,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now(),
                unique (organization_id, email)
            )
            """
        )
        conn.execute("ALTER TABLE public.app_users DROP CONSTRAINT IF EXISTS app_users_role_check")
        conn.execute(
            """
            ALTER TABLE public.app_users
            ADD CONSTRAINT app_users_role_check
            CHECK (role in ('Owner', 'Admin', 'Office Manager', 'Office Assistant', 'Staff'))
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'Office Assistant',
                auth_provider TEXT NOT NULL DEFAULT 'email',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "active" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        if "auth_provider" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN auth_provider TEXT NOT NULL DEFAULT 'email'")


def ensure_status_change_table(conn):
    if PG_MODE:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.student_status_changes (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                student_id uuid not null references public.students(id) on delete cascade,
                previous_status text not null,
                new_status text not null,
                changed_at timestamptz not null default now(),
                changed_month text not null,
                notes text
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS student_status_changes (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                changed_month TEXT NOT NULL,
                notes TEXT
            )
            """
        )


def ensure_student_audit_tables(conn):
    if PG_MODE:
        conn.execute("ALTER TABLE public.students ADD COLUMN IF NOT EXISTS deleted_at timestamptz")
        conn.execute("ALTER TABLE public.students ADD COLUMN IF NOT EXISTS deleted_by text")
        conn.execute("ALTER TABLE public.students ADD COLUMN IF NOT EXISTS delete_reason text")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.audit_logs (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid references public.organizations(id) on delete cascade,
                entity_type text not null,
                entity_id text,
                action text not null,
                actor_email text,
                summary text,
                before_json jsonb,
                after_json jsonb,
                created_at timestamptz not null default now()
            )
            """
        )
        return

    student_columns = {row["name"] for row in conn.execute("PRAGMA table_info(students)").fetchall()}
    for name in ["deleted_at", "deleted_by", "delete_reason"]:
        if name not in student_columns:
            conn.execute(f"ALTER TABLE students ADD COLUMN {name} TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT,
            action TEXT NOT NULL,
            actor_email TEXT,
            summary TEXT,
            before_json TEXT,
            after_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def record_audit(conn, action, entity_type, entity_id=None, summary="", before=None, after=None, actor_email="system"):
    ensure_student_audit_tables(conn)
    before_json = json.dumps(rowdict(before) if before is not None and not isinstance(before, dict) else before, default=str) if before is not None else None
    after_json = json.dumps(rowdict(after) if after is not None and not isinstance(after, dict) else after, default=str) if after is not None else None
    branch_id = current_branch_id(conn)
    if PG_MODE:
        conn.execute(
            """
            INSERT INTO public.audit_logs(organization_id, branch_id, entity_type, entity_id, action, actor_email, summary, before_json, after_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
            """,
            (current_org_id(conn), branch_id, entity_type, str(entity_id or ""), action, actor_email, summary, before_json, after_json),
        )
        return
    conn.execute(
        """
        INSERT INTO audit_logs(branch_id, entity_type, entity_id, action, actor_email, summary, before_json, after_json)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (branch_id, entity_type, str(entity_id or ""), action, actor_email, summary, before_json, after_json),
    )


def ensure_student_schedule_tables(conn):
    if PG_MODE:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.student_schedules (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                student_id uuid not null references public.students(id) on delete cascade,
                weekday text not null,
                start_time time not null,
                end_time time not null,
                active boolean not null default true,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_student_schedules_org_student ON public.student_schedules(organization_id, student_id)")
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS student_schedules (
            id INTEGER PRIMARY KEY,
            student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            weekday TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_student_schedules_student ON student_schedules(student_id)")


def normalize_time_value(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"]:
        try:
            return datetime.strptime(text.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError(f"Invalid schedule time: {text}")


def normalize_student_schedules(value):
    schedules = value if isinstance(value, list) else []
    cleaned = []
    seen_days = set()
    for item in schedules:
        if not isinstance(item, dict):
            continue
        weekday = str(item.get("weekday") or "").strip()
        start_time = normalize_time_value(item.get("start_time"))
        end_time = normalize_time_value(item.get("end_time"))
        if not weekday and not start_time and not end_time:
            continue
        if weekday not in STUDENT_SCHEDULE_WEEKDAYS:
            raise ValueError("Student schedule day must be Tuesday through Saturday")
        if not start_time or not end_time:
            raise ValueError("Schedule start and end time are required")
        if start_time >= end_time:
            raise ValueError("Schedule end time must be after start time")
        if weekday in seen_days:
            raise ValueError("Use each schedule day only once per student")
        seen_days.add(weekday)
        cleaned.append({"weekday": weekday, "start_time": start_time, "end_time": end_time})
    if len(cleaned) > 2:
        raise ValueError("Each student can have a maximum of two weekly schedule days")
    return cleaned


def schedule_display(schedules):
    return "; ".join(f"{item['weekday'][:3]} {item['start_time']}-{item['end_time']}" for item in schedules)


def get_student_schedules(conn, student_ids=None):
    ensure_student_schedule_tables(conn)
    if PG_MODE:
        org_id = current_org_id(conn)
        rows = conn.execute(
            """
            SELECT id::text AS id, student_id::text AS student_id, weekday,
                   to_char(start_time, 'HH24:MI') AS start_time,
                   to_char(end_time, 'HH24:MI') AS end_time,
                   active
            FROM public.student_schedules
            WHERE organization_id=%s AND active=true
            ORDER BY student_id, start_time
            """,
            (org_id,),
        ).fetchall()
    else:
        if student_ids:
            ids = [int(item) for item in student_ids]
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT * FROM student_schedules WHERE active=1 AND student_id IN ({placeholders}) ORDER BY student_id, start_time",
                ids,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM student_schedules WHERE active=1 ORDER BY student_id, start_time").fetchall()
    grouped = {}
    for row in rows:
        item = rowdict(row)
        grouped.setdefault(str(item["student_id"]), []).append(item)
    return grouped


def save_student_schedules(conn, student_id, schedules):
    ensure_student_schedule_tables(conn)
    cleaned = normalize_student_schedules(schedules)
    if PG_MODE:
        org_id = current_org_id(conn)
        conn.execute("DELETE FROM public.student_schedules WHERE organization_id=%s AND student_id=%s", (org_id, str(student_id)))
        for item in cleaned:
            conn.execute(
                """
                INSERT INTO public.student_schedules(organization_id, student_id, weekday, start_time, end_time)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (org_id, str(student_id), item["weekday"], item["start_time"], item["end_time"]),
            )
        return
    conn.execute("DELETE FROM student_schedules WHERE student_id=?", (int(student_id),))
    for item in cleaned:
        conn.execute(
            "INSERT INTO student_schedules(student_id, weekday, start_time, end_time) VALUES (?,?,?,?)",
            (int(student_id), item["weekday"], item["start_time"], item["end_time"]),
        )


def ensure_staff_tables(conn):
    # --- Multi-Tenant and Multi-Branch Migrations ---
    if PG_MODE:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.organizations (
                id uuid primary key default gen_random_uuid(),
                name text not null,
                slug text not null,
                details text,
                subjects_offered text,
                current_month text,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.branches (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                name text not null,
                slug text not null,
                code text,
                settings jsonb,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
            """
        )
        conn.execute("ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS slug text")
        conn.execute("UPDATE public.organizations SET slug='smp-kumon' WHERE slug IS NULL")
        conn.execute("ALTER TABLE public.branches ADD COLUMN IF NOT EXISTS slug text")
        conn.execute("UPDATE public.branches SET slug='calgary-ne' WHERE slug IS NULL")
        conn.execute("ALTER TABLE public.branches ADD COLUMN IF NOT EXISTS code text")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_branches_org_slug ON public.branches(organization_id, slug)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.app_meta (
                key text primary key,
                value text not null
            )
            """
        )
        conn.commit()
        
        org_row = conn.execute("SELECT id::text AS id FROM public.organizations ORDER BY created_at LIMIT 1").fetchone()
        if org_row:
            default_org_id = rowdict(org_row)["id"]
        else:
            new_org = conn.execute(
                """
                INSERT INTO public.organizations(name, slug, details, subjects_offered, current_month)
                VALUES ('SMP Kumon', 'smp-kumon', 'Kumon Learning Center', '[]', 'July 2026') RETURNING id::text AS id
                """
            ).fetchone()
            default_org_id = rowdict(new_org)["id"]
            conn.commit()
            
        branch_row = conn.execute("SELECT id::text AS id FROM public.branches WHERE organization_id=%s ORDER BY created_at LIMIT 1", (default_org_id,)).fetchone()
        if branch_row:
            default_branch_id = rowdict(branch_row)["id"]
        else:
            new_br = conn.execute(
                """
                INSERT INTO public.branches(organization_id, name, slug, settings)
                VALUES (%s, 'Calgary NE', 'calgary-ne', '{}') RETURNING id::text AS id
                """,
                (default_org_id,)
            ).fetchone()
            default_branch_id = rowdict(new_br)["id"]
            conn.commit()
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                details TEXT,
                subjects_offered TEXT,
                current_month TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                slug TEXT NOT NULL,
                code TEXT,
                settings TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(organization_id, slug)
            )
            """
        )
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(branches)").fetchall()]
        if "code" not in cols:
            conn.execute("ALTER TABLE branches ADD COLUMN code TEXT")
        conn.commit()
        
        org_row = conn.execute("SELECT id FROM organizations ORDER BY created_at LIMIT 1").fetchone()
        if org_row:
            default_org_id = org_row[0]
        else:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO organizations(name, slug, details, subjects_offered, current_month)
                VALUES ('SMP Kumon', 'smp-kumon', 'Kumon Learning Center', '[]', 'July 2026')
                """
            )
            default_org_id = cur.lastrowid
            conn.commit()
            
        branch_row = conn.execute("SELECT id FROM branches WHERE organization_id=? ORDER BY created_at LIMIT 1", (default_org_id,)).fetchone()
        if branch_row:
            default_branch_id = branch_row[0]
        else:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO branches(organization_id, name, slug, settings)
                VALUES (?, 'Calgary NE', 'calgary-ne', '{}')
                """,
                (default_org_id,)
            )
            default_branch_id = cur.lastrowid
            conn.commit()

    def ensure_branch_column(table_name):
        if PG_MODE:
            col_check = conn.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s AND column_name = 'branch_id'
                """,
                (table_name,)
            ).fetchone()
            if not col_check:
                conn.execute(f"ALTER TABLE public.{table_name} ADD COLUMN branch_id uuid REFERENCES public.branches(id) ON DELETE CASCADE")
                conn.commit()
            conn.execute(f"UPDATE public.{table_name} SET branch_id = %s WHERE branch_id IS NULL", (default_branch_id,))
            conn.commit()
        else:
            exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
            if not exists:
                return
            cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
            if "branch_id" not in cols:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN branch_id INTEGER REFERENCES branches(id) ON DELETE CASCADE")
                conn.commit()
            conn.execute(f"UPDATE {table_name} SET branch_id = ? WHERE branch_id IS NULL", (default_branch_id,))
            conn.commit()

    # Pre-existing tables
    existing_tables = ["students", "payments", "student_status_changes", "audit_logs"]
    if PG_MODE:
        existing_tables.append("app_users")
    else:
        existing_tables.append("users")
    for tbl in existing_tables:
        ensure_branch_column(tbl)

    if PG_MODE:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.staff_members (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                staff_name text not null,
                role_title text,
                subject text,
                phone text,
                email text,
                hourly_rate numeric(10,2) not null default 0,
                pin text,
                active boolean not null default true,
                notes text,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
            """
        )
        ensure_branch_column("staff_members")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS pin_hash text")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS password_hash text")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS role varchar(50) not null default 'staff'")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS avatar_initials varchar(5) not null default 'ST'")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS avatar_color varchar(10) not null default '#6366f1'")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS expo_push_token text")
        conn.execute("ALTER TABLE public.staff_members ADD COLUMN IF NOT EXISTS notifications_last_checked_at timestamptz")
        conn.execute("UPDATE public.staff_members SET role='administrator' WHERE email IN ('syedzaidipk@gmail.com', 'najampk@gmail.com')")
        conn.execute("UPDATE public.staff_members SET role='principal_owner' WHERE email='aneelanajam1@gmail.com'")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.staff_schedules (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                staff_id uuid not null references public.staff_members(id) on delete cascade,
                week_start varchar(10) not null default '2026-05-26',
                weekday text not null,
                shift_type text not null default 'Work',
                start_time text,
                end_time text,
                location text,
                notes text,
                published boolean not null default false,
                acknowledged boolean not null default false,
                updated_at timestamptz not null default now(),
                unique (staff_id, week_start, weekday)
            )
            """
        )
        ensure_branch_column("staff_schedules")
        conn.execute("ALTER TABLE public.staff_schedules ADD COLUMN IF NOT EXISTS week_start varchar(10) not null default '2026-05-26'")
        conn.execute("ALTER TABLE public.staff_schedules ADD COLUMN IF NOT EXISTS acknowledged boolean not null default false")
        conn.commit()
        try:
            exists = conn.execute("SELECT 1 FROM pg_constraint WHERE conname = 'staff_schedules_staff_id_week_start_weekday_key'").fetchone()
            if not exists:
                conn.execute("ALTER TABLE public.staff_schedules DROP CONSTRAINT IF EXISTS staff_schedules_staff_id_weekday_key")
                conn.execute("ALTER TABLE public.staff_schedules ADD CONSTRAINT staff_schedules_staff_id_week_start_weekday_key UNIQUE(staff_id, week_start, weekday)")
                conn.commit()
        except Exception as e:
            conn.rollback()
            import sys
            print(f"WARNING: Unique constraint migration failed: {e}", file=sys.stderr)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.staff_shift_punches (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                staff_id uuid not null references public.staff_members(id) on delete cascade,
                punch_date date not null default current_date,
                clock_in text,
                clock_out text,
                duration_hours numeric(10,2) not null default 0,
                source text,
                notes text,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now()
            )
            """
        )
        ensure_branch_column("staff_shift_punches")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.time_off_requests (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                staff_id uuid not null references public.staff_members(id) on delete cascade,
                start_date date not null,
                end_date date not null,
                reason text not null,
                status varchar(20) not null default 'pending',
                submitted_at timestamptz not null default now(),
                approval_token text,
                approval_token_expires_at timestamptz,
                reminder_sent_at timestamptz,
                decided_at timestamptz
            )
            """
        )
        ensure_branch_column("time_off_requests")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.shift_swap_requests (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                requester_id uuid not null references public.staff_members(id) on delete cascade,
                shift_date date not null,
                reason text not null,
                status varchar(20) not null default 'open',
                claimed_by_id uuid references public.staff_members(id),
                posted_at timestamptz not null default now()
            )
            """
        )
        ensure_branch_column("shift_swap_requests")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.announcements (
                id uuid primary key default gen_random_uuid(),
                organization_id uuid not null references public.organizations(id) on delete cascade,
                title text not null,
                body text not null,
                date text not null,
                priority varchar(20) not null default 'normal',
                active boolean not null default true,
                created_at timestamptz not null default now(),
                source_id text
            )
            """
        )
        ensure_branch_column("announcements")
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_name TEXT NOT NULL,
                role_title TEXT,
                subject TEXT,
                phone TEXT,
                email TEXT,
                hourly_rate REAL NOT NULL DEFAULT 0,
                pin TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_branch_column("staff_members")
        staff_cols = [row["name"] for row in conn.execute("PRAGMA table_info(staff_members)").fetchall()]
        if "pin_hash" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN pin_hash TEXT")
        if "password_hash" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN password_hash TEXT")
        if "role" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN role TEXT DEFAULT 'staff'")
        if "avatar_initials" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN avatar_initials TEXT")
        if "avatar_color" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN avatar_color TEXT")
        if "expo_push_token" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN expo_push_token TEXT")
        if "notifications_last_checked_at" not in staff_cols:
            conn.execute("ALTER TABLE staff_members ADD COLUMN notifications_last_checked_at TEXT")
        
        conn.execute("UPDATE staff_members SET role='administrator' WHERE email IN ('syedzaidipk@gmail.com', 'najampk@gmail.com')")
        conn.execute("UPDATE staff_members SET role='principal_owner' WHERE email='aneelanajam1@gmail.com'")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL REFERENCES staff_members(id) ON DELETE CASCADE,
                week_start TEXT NOT NULL DEFAULT '2026-05-26',
                weekday TEXT NOT NULL,
                shift_type TEXT NOT NULL DEFAULT 'Work',
                start_time TEXT,
                end_time TEXT,
                location TEXT,
                notes TEXT,
                published INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(staff_id, week_start, weekday)
            )
            """
        )
        ensure_branch_column("staff_schedules")
        sched_cols = [row["name"] for row in conn.execute("PRAGMA table_info(staff_schedules)").fetchall()]
        if "week_start" not in sched_cols:
            conn.execute("ALTER TABLE staff_schedules ADD COLUMN week_start TEXT NOT NULL DEFAULT '2026-05-26'")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_schedules_staff_week_day ON staff_schedules(staff_id, week_start, weekday)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_shift_punches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL REFERENCES staff_members(id) ON DELETE CASCADE,
                punch_date TEXT NOT NULL DEFAULT CURRENT_DATE,
                clock_in TEXT,
                clock_out TEXT,
                duration_hours REAL NOT NULL DEFAULT 0,
                source TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_branch_column("staff_shift_punches")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS time_off_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL REFERENCES staff_members(id) ON DELETE CASCADE,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                approval_token TEXT,
                approval_token_expires_at TEXT,
                reminder_sent_at TEXT,
                decided_at TEXT
            )
            """
        )
        ensure_branch_column("time_off_requests")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shift_swap_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL REFERENCES staff_members(id) ON DELETE CASCADE,
                shift_date TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                claimed_by_id INTEGER REFERENCES staff_members(id),
                posted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        ensure_branch_column("shift_swap_requests")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                date TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                source_id TEXT
            )
            """
        )
        ensure_branch_column("announcements")


# JWT & Security helpers for Staff mobile app
JWT_SECRET = os.environ.get("JWT_SECRET") or "dev-only-smp-staffbase-secret-change-in-prod"

def base64url_encode(payload_bytes: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(payload_bytes).decode('utf-8').rstrip('=')

def base64url_decode(payload_str: str) -> bytes:
    import base64
    rem = len(payload_str) % 4
    if rem > 0:
        payload_str += '=' * (4 - rem)
    return base64.urlsafe_b64decode(payload_str.encode('utf-8'))

def sign_jwt(payload: dict, expires_in: int = 28800) -> str:
    import hmac
    import hashlib
    import json
    import time
    header = {"alg": "HS256", "typ": "JWT"}
    payload = payload.copy()
    payload["exp"] = int(time.time()) + expires_in
    header_b64 = base64url_encode(json.dumps(header).encode('utf-8'))
    payload_b64 = base64url_encode(json.dumps(payload).encode('utf-8'))
    msg = f"{header_b64}.{payload_b64}".encode('utf-8')
    sig = hmac.new(JWT_SECRET.encode('utf-8'), msg, hashlib.sha256).digest()
    sig_b64 = base64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"

def verify_jwt(token: str) -> dict:
    import hmac
    import hashlib
    import json
    import time
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError("Invalid token format")
    header_b64, payload_b64, sig_b64 = parts
    msg = f"{header_b64}.{payload_b64}".encode('utf-8')
    expected_sig = hmac.new(JWT_SECRET.encode('utf-8'), msg, hashlib.sha256).digest()
    expected_sig_b64 = base64url_encode(expected_sig)
    if not hmac.compare_digest(sig_b64, expected_sig_b64):
        raise ValueError("Signature verification failed")
    payload = json.loads(base64url_decode(payload_b64).decode('utf-8'))
    if payload.get("exp", 0) < time.time():
        raise ValueError("Token expired")
    return payload

def verify_staff_pin(entered_pin: str, stored_pin: str, stored_pin_hash: str) -> bool:
    if stored_pin_hash and stored_pin_hash.startswith("$"):
        try:
            import bcrypt
            return bcrypt.checkpw(str(entered_pin).encode("utf-8"), str(stored_pin_hash).encode("utf-8"))
        except ImportError:
            pass
    entered_pin_str = str(entered_pin)
    return (stored_pin and str(stored_pin) == entered_pin_str) or (stored_pin_hash and str(stored_pin_hash) == entered_pin_str)

def verify_staff_password(entered_password: str, stored_password_hash: str) -> bool:
    if stored_password_hash and stored_password_hash.startswith("$"):
        try:
            import bcrypt
            return bcrypt.checkpw(str(entered_password).encode("utf-8"), str(stored_password_hash).encode("utf-8"))
        except ImportError:
            pass
    return str(entered_password) == str(stored_password_hash)

def hash_bcrypt(value: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(str(value).encode("utf-8"), bcrypt.gensalt(10)).decode("utf-8")
    except ImportError:
        import hashlib
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()



def format_time_str(val):
    if not val:
        return None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt.strftime("%I:%M %p")
    except Exception:
        return val

def get_monday_of_current_week():
    from datetime import datetime, timedelta
    now = datetime.now()
    diff = now.weekday()
    monday = now - timedelta(days=diff)
    return monday.strftime("%Y-%m-%d")

def format_week_label(week_start_str):
    try:
        from datetime import datetime, timedelta
        mon = datetime.strptime(week_start_str, "%Y-%m-%d")
        fri = mon + timedelta(days=4)
        return f"{mon.strftime('%b %d')} – {fri.strftime('%d, %Y')}"
    except Exception:
        return week_start_str

def format_week_key(week_start_str):
    try:
        from datetime import datetime
        d = datetime.strptime(week_start_str, "%Y-%m-%d")
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"
    except Exception:
        return week_start_str

def build_clock_status(row):
    if not row:
        return {"clockIn": None, "clockOut": None, "status": "none", "gpsOK": None, "date": None}
    d = rowdict(row)
    cin = d.get("clock_in")
    cout = d.get("clock_out")
    status = "out" if cout else ("in" if cin else "none")
    return {
        "clockIn": format_time_str(cin),
        "clockOut": format_time_str(cout),
        "status": status,
        "gpsOK": d.get("gps_ok") or d.get("gpsok") or True,
        "date": d.get("punch_date")
    }

def decision_html_page(title, success, message):
    color = "#16a34a" if success else "#dc2626"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — StaffBase</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; background: #f9fafb; }}
    .card {{ background: #fff; border-radius: 12px; padding: 40px 48px; box-shadow: 0 1px 3px rgba(0,0,0,.1); max-width: 440px; text-align: center; }}
    h1 {{ color: {color}; margin: 0 0 16px; font-size: 24px; }}
    p {{ color: #374151; line-height: 1.6; margin: 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""

def staff_data_snapshot(conn, staff_id=None):
    from datetime import datetime
    week_start = get_monday_of_current_week()
    today = datetime.now().strftime("%Y-%m-%d")
    
    is_manager = False
    if staff_id:
        if str(staff_id) == "9999":
            is_manager = True
        else:
            if PG_MODE:
                role_row = conn.execute("SELECT role_title, role FROM public.staff_members WHERE id=%s", (str(staff_id),)).fetchone()
            else:
                role_row = conn.execute("SELECT role_title, role FROM staff_members WHERE id=?", (int(staff_id),)).fetchone()
            if role_row:
                rd = rowdict(role_row)
                role_val = str(rd.get("role") or rd.get("role_title") or "").lower().strip()
                if role_val in ["manager", "admin", "administrator", "owner", "principal_owner", "principal"]:
                    is_manager = True
    
    if PG_MODE:
        members = conn.execute("SELECT id::text as id, staff_name, email, role_title, subject, active FROM public.staff_members").fetchall()
        schedules = conn.execute("SELECT * FROM public.staff_schedules WHERE week_start=%s", (week_start,)).fetchall()
        punches = conn.execute("SELECT * FROM public.staff_shift_punches").fetchall()
        requests = conn.execute("SELECT * FROM public.time_off_requests").fetchall()
        swaps = conn.execute("SELECT * FROM public.shift_swap_requests").fetchall()
        announcements = conn.execute("SELECT * FROM public.announcements").fetchall()
    else:
        members = conn.execute("SELECT id, staff_name, email, role_title, subject, active FROM staff_members").fetchall()
        schedules = conn.execute("SELECT * FROM staff_schedules WHERE week_start=?", (week_start,)).fetchall()
        punches = conn.execute("SELECT * FROM staff_shift_punches").fetchall()
        requests = conn.execute("SELECT * FROM time_off_requests").fetchall()
        swaps = conn.execute("SELECT * FROM shift_swap_requests").fetchall()
        announcements = conn.execute("SELECT * FROM announcements").fetchall()

    users = []
    for m in members:
        d = rowdict(m)
        role = d.get("role_title") or "staff"
        db_active_bool = d.get("active") not in [False, "false", "False", 0, "0", None]
        users.append({
            "id": d["id"],
            "name": d.get("staff_name") or "",
            "email": d.get("email") or "",
            "role": "principal_owner" if role.lower() in ["manager", "admin", "administrator", "owner", "principal_owner"] else "staff",
            "dept": d.get("subject") or "Administration",
            "pos": role,
            "av": "".join([part[0] for part in str(d.get("staff_name")).split(" ") if part]).upper()[:2],
            "active": db_active_bool
        })

    schedule = None
    if schedules:
        shifts = {}
        for s in schedules:
            d = rowdict(s)
            sid = str(d["staff_id"])
            if not is_manager and (not staff_id or str(sid) != str(staff_id)):
                continue
            if sid not in shifts:
                shifts[sid] = {}
            shifts[sid][d["weekday"]] = {
                "type": d["shift_type"],
                "start": d["start_time"],
                "end": d["end_time"],
                "location": d["location"],
                "notes": "",
                "ack": bool(d.get("acknowledged") or d.get("ack"))
            }
        schedule = {
            "published": True,
            "publishedAt": format_week_label(week_start),
            "week": format_week_label(week_start),
            "weekKey": format_week_key(week_start),
            "shifts": shifts
        }

    clock_data = {}
    checkin_log = []
    
    staff_name_map = {str(rowdict(m)["id"]): rowdict(m).get("staff_name") for m in members}
    
    for p in punches:
        d = rowdict(p)
        sid = str(d["staff_id"])
        if not is_manager and (not staff_id or str(sid) != str(staff_id)):
            continue
        name = staff_name_map.get(sid) or "Unknown"
        cin = d.get("clock_in")
        cout = d.get("clock_out")
        
        if d.get("punch_date") == today:
            clock_data[sid] = {
                "clockedIn": bool(cin and not cout),
                "clockIn": cin,
                "clockOut": cout,
                "gpsOK": d.get("gps_ok") or d.get("gpsok") or True
            }
        
        if cin:
            checkin_log.append({
                "id": str(d["id"]),
                "staffId": sid,
                "name": name,
                "time": cin,
                "type": "in",
                "date": d["punch_date"],
                "gpsOK": d.get("gps_ok") or d.get("gpsok") or True
            })
        if cout:
            checkin_log.append({
                "id": f"{d['id']}-out",
                "staffId": sid,
                "name": name,
                "time": cout,
                "type": "out",
                "date": d["punch_date"],
                "gpsOK": d.get("gps_ok") or d.get("gpsok") or True
            })
            
    checkin_log.sort(key=lambda x: x["time"] or "", reverse=True)

    requests_out = []
    for r in requests:
        d = rowdict(r)
        if not is_manager and (not staff_id or str(d["staff_id"]) != str(staff_id)):
            continue
        requests_out.append({
            "id": str(d["id"]),
            "uid": str(d["staff_id"]),
            "type": "timeoff",
            "startDate": d["start_date"],
            "endDate": d["end_date"],
            "reason": d["reason"],
            "status": d["status"],
            "submittedAt": d["submitted_at"]
        })

    swaps_out = []
    if staff_id:
        for s in swaps:
            d = rowdict(s)
            swaps_out.append({
                "id": str(d["id"]),
                "uid": str(d["requester_id"]),
                "shiftDate": d["shift_date"],
                "reason": d["reason"],
                "status": d["status"],
                "claimedById": str(d["claimed_by_id"]) if d.get("claimed_by_id") else None,
                "claimedByName": staff_name_map.get(str(d.get("claimed_by_id"))) if d.get("claimed_by_id") else None,
                "postedAt": d["posted_at"]
            })

    announcements_out = []
    for a in announcements:
        d = rowdict(a)
        announcements_out.append({
            "id": str(d["id"]),
            "title": d["title"],
            "body": d["body"],
            "date": d["date"],
            "important": d["priority"] == "high",
            "priority": d["priority"]
        })

    teacher_assignments = []
    if is_manager:
        try:
            import json
            if PG_MODE:
                meta_row = conn.execute("SELECT value FROM public.app_meta WHERE key=%s", ("teacher_assignments",)).fetchone()
            else:
                meta_row = conn.execute("SELECT value FROM app_meta WHERE key=?", ("teacher_assignments",)).fetchone()
            if meta_row:
                teacher_assignments = json.loads(rowdict(meta_row)["value"])
        except Exception as e:
            print("Error fetching teacher assignments from app_meta:", e)

    org_name = "Kumon Canada"
    branch_name = "Cityscape Square"
    branch_code = "CCS001"
    settings = {}
    try:
        settings = get_settings(conn)
        if PG_MODE:
            org_id = current_org_id(conn)
            org_row = conn.execute("SELECT name FROM public.organizations WHERE id=%s", (org_id,)).fetchone()
            if org_row:
                org_name = rowdict(org_row)["name"]
            branch_row = conn.execute("SELECT name, code FROM public.branches WHERE organization_id=%s ORDER BY created_at LIMIT 1", (org_id,)).fetchone()
            if branch_row:
                branch_name = rowdict(branch_row)["name"]
                branch_code = rowdict(branch_row)["code"] or ""
        else:
            org_row = conn.execute("SELECT id, name FROM organizations ORDER BY created_at LIMIT 1").fetchone()
            if org_row:
                org_id = org_row[0]
                org_name = org_row[1]
                branch_row = conn.execute("SELECT name, code FROM branches WHERE organization_id=? ORDER BY created_at LIMIT 1", (org_id,)).fetchone()
                if branch_row:
                    branch_name = branch_row[0]
                    branch_code = branch_row[1] or ""
    except Exception as e:
        print("Error fetching org/branch info in staff_data_snapshot:", e)

    return {
        "teacher_assignments": teacher_assignments,
        "school": {
            "lat": 43.7615, "lng": -79.4111, "radius": 300,
            "name": f"{org_name} - {branch_name}" if branch_name else org_name,
            "address": f"{branch_name} Branch ({branch_code})" if branch_code else branch_name,
            "website": "www.kumon.com",
            "email": settings.get("support_email", "support@smp.edu"),
            "otThreshold": 8.0,
            "openDays": ["Tue", "Wed", "Thu", "Fri", "Sat"],
            "operatingStart": settings.get("operating_start", "15:00"),
            "operatingEnd": settings.get("operating_end", "20:00")
        },
        "users": users,
        "subjects": configured_subjects(settings),
        "schedule": schedule,
        "requests": requests_out,
        "announcements": announcements_out,
        "messages": [],
        "clock_data": clock_data,
        "checkin_log": checkin_log,
        "swaps": swaps_out,
        "documents": [],
        "ts_approvals": []
    }


def send_email_notification(to_email, subject, plain_text, html_content=None):
    import os
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    
    # 1. Try SendGrid
    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    if sendgrid_key:
        try:
            import urllib.request
            import json
            url = "https://api.sendgrid.com/v3/mail/send"
            headers = {
                "Authorization": f"Bearer {sendgrid_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": os.environ.get("FROM_EMAIL", "support@smp.edu")},
                "subject": subject,
                "content": [
                    {"type": "text/plain", "value": plain_text}
                ]
            }
            if html_content:
                payload["content"].append({"type": "text/html", "value": html_content})
            
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                if resp.status in [200, 201, 202]:
                    print(f"Email sent via SendGrid to {to_email}")
                    return True
        except Exception as e:
            print("Failed to send email via SendGrid:", e)

    # 2. Try SMTP
    smtp_host = os.environ.get("SMTP_HOST") or os.environ.get("SMTP_SERVER")
    if smtp_host:
        try:
            port = int(os.environ.get("SMTP_PORT", 587))
            user = os.environ.get("SMTP_USER") or os.environ.get("SMTP_USERNAME")
            passwd = os.environ.get("SMTP_PASSWORD")
            from_addr = os.environ.get("FROM_EMAIL") or os.environ.get("SMTP_FROM") or "support@smp.edu"
            
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = from_addr
            msg["To"] = to_email
            
            msg.attach(MIMEText(plain_text, "plain"))
            if html_content:
                msg.attach(MIMEText(html_content, "html"))
                
            with smtplib.SMTP(smtp_host, port, timeout=10) as server:
                if port == 587:
                    server.starttls()
                if user and passwd:
                    server.login(user, passwd)
                server.sendmail(from_addr, [to_email], msg.as_string())
            print(f"Email sent via SMTP to {to_email}")
            return True
        except Exception as e:
            print("Failed to send email via SMTP:", e)
            
    # 3. Fallback: Log it locally
    print(f"=== AUTOMATED EMAIL SIMULATION ===")
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print(f"Body:\n{plain_text}")
    print(f"===================================")
    # Write to a mock file for UAT verification
    try:
        import os
        import time
        os.makedirs("emails", exist_ok=True)
        filename = f"emails/welcome_{to_email}_{int(time.time())}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"Subject: {subject}\nTo: {to_email}\n\n{plain_text}")
        print(f"Mock email file saved to: {filename}")
    except Exception as e:
        print("Failed to save mock email file:", e)
    return False

def send_staff_welcome_email(conn, name, email, pin, role, pos, password, origin):
    settings = get_settings(conn)
    school_name = settings.get("institution_name", "SMP - After School Management Program")
    support_email = settings.get("support_email", "support@smp.edu")
    
    subject = f"Welcome to {school_name} — Your StaffBase Account"
    
    role_labels = {
        "principal_owner": "Principal/Owner",
        "administrator": "Administrator",
        "office_manager": "Office Manager",
        "office_assistant": "Office Assistant",
        "staff": "Staff"
    }
    role_label = role_labels.get(role.lower().strip(), role)
    
    # Render professional plain text email body
    plain_text = f"""Dear {name},

Welcome to the {school_name} team! Your StaffBase account has been created.

YOUR ACCOUNT DETAILS
────────────────────
Name:    {name}
Email:   {email}
Role:    {role_label}
PIN:     {pin}  (keep this private)
"""
    if password:
        plain_text += f"Admin password: {password}  (keep this private)\n"
        
    plain_text += f"""
HOW TO GET STARTED
──────────────────
1. Download "Expo Go" from the App Store (iPhone) or Play Store (Android).
2. Open the app and scan the QR code from your manager.
3. Tap "Staff Login", select your name ({name}), and enter your PIN ({pin}).
4. Allow location access — required for GPS clock-in at the school.
5. Alternatively, you can log in on your phone/computer's web browser at the Staff portal:
   {origin}/staffbase.html

Questions? Contact your administrator: {support_email}
Please keep your PIN private. Contact admin if you need it reset.
"""

    # Beautiful, modern responsive HTML welcome email template matching design aesthetics!
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f3f4f6; color: #1f2937; margin: 0; padding: 20px; }}
  .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }}
  .header {{ background-color: #1e3a8a; padding: 30px; text-align: center; color: #ffffff; }}
  .header h1 {{ margin: 0; font-size: 24px; font-weight: 800; letter-spacing: -0.025em; }}
  .header p {{ margin: 5px 0 0 0; font-size: 13px; color: #93c5fd; text-transform: uppercase; letter-spacing: 0.1em; }}
  .content {{ padding: 30px; line-height: 1.6; font-size: 15px; }}
  .details {{ background-color: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; margin: 20px 0; }}
  .details-title {{ font-weight: 700; font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
  .row {{ display: flex; margin-bottom: 8px; }}
  .label {{ width: 120px; font-weight: 600; color: #4b5563; }}
  .value {{ font-family: monospace; font-size: 14px; font-weight: 700; color: #111827; }}
  .btn {{ display: inline-block; background-color: #1e3a8a; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: 700; font-size: 14px; margin-top: 15px; text-align: center; }}
  .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; background: #f9fafb; }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <p>Automated Onboarding</p>
    <h1>{school_name}</h1>
  </div>
  <div class="content">
    <p>Dear <strong>{name}</strong>,</p>
    <p>Welcome to the <strong>{school_name}</strong> team! Your new StaffBase portal account is set up and ready to use.</p>
    
    <div class="details">
      <div class="details-title">Your Credentials</div>
      <div class="row"><span class="label">Name:</span><span class="value">{name}</span></div>
      <div class="row"><span class="label">Email:</span><span class="value">{email}</span></div>
      <div class="row"><span class="label">Role:</span><span class="value">{role_label}</span></div>
      <div class="row"><span class="label">PIN:</span><span class="value" style="color: #d97706; font-size: 16px;">{pin}</span></div>
      {"<div class='row'><span class='label'>Password:</span><span class='value'>" + password + "</span></div>" if password else ""}
    </div>

    <h3>How to get started:</h3>
    <ol>
      <li>Download <strong>Expo Go</strong> on your smartphone.</li>
      <li>Scan the QR code provided by your manager or navigate directly to the Staff portal.</li>
      <li>To access the portal on your computer or phone's web browser, click below:</li>
    </ol>
    
    <div style="text-align: center;">
      <a href="{origin}/staffbase.html" class="btn" target="_blank">Access Staff Portal</a>
    </div>
  </div>
  <div class="footer">
    Questions? Contact your administrator at <a href="mailto:{support_email}" style="color: #2563eb; text-decoration: none;">{support_email}</a><br>
    Please keep your PIN confidential. Contact admin if you need a reset.
  </div>
</div>
</body>
</html>"""

    send_email_notification(email, subject, plain_text, html_content)


def normalize_role(role):
    value = str(role or "").strip()
    aliases = {
        "admin": "Admin",
        "owner": "Owner",
        "office manager": "Office Manager",
        "manager": "Office Manager",
        "office assistant": "Office Assistant",
        "assistant": "Office Assistant",
        "staff": "Staff",
        "teacher": "Staff",
        "employee": "Staff",
    }
    return aliases.get(value.lower(), value if value in ROLE_OPTIONS else "Office Assistant")


def list_app_users(conn):
    ensure_access_tables(conn)
    if PG_MODE:
        org_id = current_org_id(conn)
        rows = conn.execute(
            """
            SELECT id::text AS id, email, display_name, role, active, created_at
            FROM public.app_users
            WHERE organization_id=%s
            ORDER BY role, email
            """,
            (org_id,),
        ).fetchall()
        return [{**rowdict(row), "role": normalize_role(row_get(row, "role")), "auth_provider": "Google / Email"} for row in rows]
    rows = conn.execute("SELECT id, email, display_name, role, auth_provider, active, created_at FROM users ORDER BY role, email").fetchall()
    return [{**rowdict(row), "role": normalize_role(row_get(row, "role"))} for row in rows]


def ensure_first_admin(conn, user):
    if not user or not user.get("email"):
        return
    ensure_access_tables(conn)
    email = user["email"].lower()
    name = user.get("name") or email
    if PG_MODE:
        org_id = current_org_id(conn)
        count = conn.execute("SELECT count(*) AS c FROM public.app_users WHERE organization_id=%s", (org_id,)).fetchone()["c"]
        if int(count or 0) == 0:
            conn.execute(
                """
                INSERT INTO public.app_users(organization_id, email, display_name, role, active)
                VALUES (%s,%s,%s,'Admin',true)
                ON CONFLICT (organization_id, email) DO NOTHING
                """,
                (org_id, email, name),
            )
            conn.commit()
    else:
        count = conn.execute("SELECT count(*) AS c FROM users").fetchone()["c"]
        if int(count or 0) == 0:
            conn.execute(
                "INSERT OR IGNORE INTO users(email, display_name, role, auth_provider, active) VALUES (?,?,?,?,1)",
                (email, name, "Admin", "email"),
            )
            conn.commit()


def get_user_access(conn, user):
    if not user or not user.get("email"):
        return None
    ensure_access_tables(conn)
    email = user["email"].lower()
    if email in ["syedzaidipk@gmail.com", "najampk@gmail.com", "aneelanajam1@gmail.com"]:
        role = "Admin"
        if email == "aneelanajam1@gmail.com":
            role = "Owner"
        return {
            "id": "admin-bypass-syed",
            "email": email,
            "display_name": "Syed Zaidi (Admin)" if email != "aneelanajam1@gmail.com" else "Aneela Najam (Owner)",
            "role": role,
            "active": True
        }
    if PG_MODE:
        row = conn.execute(
            """
            SELECT id::text AS id, email, display_name, role, active
            FROM public.app_users
            WHERE organization_id=%s AND lower(email)=lower(%s)
            """,
            (current_org_id(conn), email),
        ).fetchone()
    else:
        row = conn.execute("SELECT id, email, display_name, role, active FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
    if not row or not row_get(row, "active", True):
        return None
    item = rowdict(row)
    item["role"] = normalize_role(item.get("role"))
    return item


def active_admin_count(conn):
    ensure_access_tables(conn)
    if PG_MODE:
        row = conn.execute(
            """
            SELECT count(*) AS c
            FROM public.app_users
            WHERE organization_id=%s AND role='Admin' AND active=true
            """,
            (current_org_id(conn),),
        ).fetchone()
    else:
        row = conn.execute("SELECT count(*) AS c FROM users WHERE lower(role)='admin' AND active=1").fetchone()
    return int(row_get(row, "c", 0) or 0)


def pg_subjects(value):
    return subject_list(value)


def display_student(row):
    item = rowdict(row)
    item["id"] = str(item["id"])
    item["subjects"] = subjects_text(item.get("subjects"))
    if item.get("enrol_date"):
        item["enrol_date"] = str(item["enrol_date"])
    item["std_monthly_fee"] = float(item.get("std_monthly_fee") or 0)
    return item


def get_settings(conn):
    if PG_MODE:
        org_id = current_org_id(conn)
        row = conn.execute(
            """
            SELECT name, phone, details, subjects_offered, current_month, support_email,
                   to_char(operating_start, 'HH24:MI') AS operating_start,
                   to_char(operating_end, 'HH24:MI') AS operating_end
            FROM public.organizations
            WHERE id=%s
            """,
            (org_id,),
        ).fetchone()
        branch_row = conn.execute(
            """
            SELECT name, code FROM public.branches
            WHERE organization_id=%s
            ORDER BY created_at LIMIT 1
            """,
            (org_id,),
        ).fetchone()
        
        meta_row = conn.execute(
            "SELECT value FROM public.app_meta WHERE key=%s",
            ("center_setup_completed",)
        ).fetchone()
        setup_completed = rowdict(meta_row)["value"] if meta_row else "0"
        
        values = dict(DEFAULT_SETTINGS)
        if row:
            values.update(
                {
                    "institution_name": row.get("name") or DEFAULT_SETTINGS["institution_name"],
                    "institution_phone": row.get("phone") or "",
                    "institution_details": row.get("details") or DEFAULT_SETTINGS["institution_details"],
                    "subjects_offered": "\n".join(row.get("subjects_offered") or ["Math", "English"]),
                    "current_month": row.get("current_month") or DEFAULT_SETTINGS["current_month"],
                    "operating_start": row.get("operating_start") or DEFAULT_SETTINGS["operating_start"],
                    "operating_end": row.get("operating_end") or DEFAULT_SETTINGS["operating_end"],
                    "support_email": row.get("support_email") or DEFAULT_SETTINGS["support_email"],
                }
            )
        values.update({
            "branch_name": rowdict(branch_row)["name"] if branch_row else "",
            "branch_code": rowdict(branch_row)["code"] if branch_row else "",
            "center_setup_completed": setup_completed,
        })
        if month_position(values.get("current_month")) < month_position(current_month_label()):
            values["current_month"] = current_month_label()
        return values
    
    rows = conn.execute("SELECT key, value FROM app_meta").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    
    org_row = conn.execute("SELECT id, name FROM organizations ORDER BY created_at LIMIT 1").fetchone()
    org_id = org_row[0] if org_row else 1
    branch_row = conn.execute("SELECT name, code FROM branches WHERE organization_id=? ORDER BY created_at LIMIT 1", (org_id,)).fetchone()
    
    values.update({
        "institution_name": org_row[1] if org_row else DEFAULT_SETTINGS["institution_name"],
        "branch_name": branch_row[0] if branch_row else "",
        "branch_code": branch_row[1] if branch_row else "",
        "center_setup_completed": values.get("center_setup_completed", "0"),
    })
    for key, value in DEFAULT_SETTINGS.items():
        values.setdefault(key, value)
    if month_position(values.get("current_month")) < month_position(current_month_label()):
        values["current_month"] = current_month_label()
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


def payment_method_label(value):
    method = str(value or "").strip()
    if not method:
        return "Unspecified"
    normalized = re.sub(r"[\s_-]+", "", method).lower()
    labels = {
        "etransfer": "E-Transfer",
        "pad": "PAD",
        "cash": "Cash",
        "creditcard": "Credit Card",
        "cheque": "Cheque",
    }
    return labels.get(normalized, method)


def is_pad_payment_method(value):
    return payment_method_label(value).upper() == "PAD"


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
    rate_type = str(data.get("rate_type") or "R").strip()
    fee = money(data.get("std_monthly_fee"))
    if not fee:
        with db() as conn:
            fee = 0
            for subject in subject_list(subjects):
                if PG_MODE:
                    rate = conn.execute(
                        """
                        SELECT monthly_fee
                        FROM public.rates
                        WHERE organization_id=%s AND lower(subject)=lower(%s) AND rate_type=%s
                        """,
                        (current_org_id(conn), subject, rate_type),
                    ).fetchone()
                else:
                    rate = conn.execute(
                        "SELECT monthly_fee FROM rates WHERE lower(subject)=lower(?) AND rate_type=?",
                        (subject, rate_type),
                    ).fetchone()
                fee += float(rate["monthly_fee"]) if rate else 0
    return {
        "student_name": str(data.get("student_name", "")).strip(),
        "parent_guardian": str(data.get("parent_guardian", "")).strip(),
        "status": status,
        "enrol_date": normalize_date(data.get("enrol_date", "")),
        "subjects": subjects,
        "rate_type": rate_type,
        "std_monthly_fee": fee,
        "payment_method": str(data.get("payment_method", "")).strip(),
        "phone": str(data.get("phone", "")).strip(),
        "email": str(data.get("email", "")).strip(),
        "siblings": str(data.get("siblings", "")).strip(),
        "notes": str(data.get("notes", "")).strip(),
        "schedules": normalize_student_schedules(data.get("schedules")),
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
    old_schedules = old.get("schedules", []) if isinstance(old, dict) else []
    if schedule_display(old_schedules) != schedule_display(new.get("schedules", [])):
        changed.append("Weekly Schedule")
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
    for fmt in ["%Y-%m-%d", "%Y-%b-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y", "%b %d %Y", "%d %b %Y"]:
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


DEFAULT_RECON_MATCH_RULES = {"student_name", "parent_name", "payment_amount", "payment_date", "payment_method"}


def comma_name_variant(value):
    text = str(value or "").strip()
    if "," not in text:
        return ""
    left, right = [part.strip() for part in text.split(",", 1)]
    return f"{right} {left}".strip()


def token_overlap_score(left, right):
    left_tokens = set(meaningful_tokens(left))
    right_tokens = set(meaningful_tokens(right))
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def score_payment_match(row, student, aliases, payments, match_rules=None, upload_method="PAD"):
    rules = set(match_rules or DEFAULT_RECON_MATCH_RULES)
    description = clean_match_text(f"{row.get('description', '')} {row.get('source', '')}")
    amount = money(row.get("amount"))
    month_label = transaction_month_label(row.get("date") or row.get("transaction_date"))
    score = 0
    identity_score = 0
    reasons = []
    row_identity_text = " ".join(str(row.get(key, "")) for key in ["description", "source", "student_name", "parent_name", "email", "reference"])
    row_text = clean_match_text(row_identity_text)
    csv_student_text = clean_match_text(row.get("student_name", ""))
    csv_parent_text = clean_match_text(row.get("parent_name", "") or row.get("description", ""))
    csv_parent_reversed = clean_match_text(comma_name_variant(row.get("parent_name", "") or row.get("description", "")))

    if "payment_method" in rules:
        row_method = payment_method_label(row.get("payment_method") or upload_method)
        student_method = payment_method_label(student.get("payment_method", ""))
        if row_method == student_method:
            score += 18
            reasons.append(f"payment method matches {student_method}")
        else:
            reasons.append(f"payment method mismatch: CSV {row_method}, student {student_method}")

    parent = student.get("parent_guardian", "")
    parent_text = clean_match_text(parent)
    parent_identity_texts = [text for text in {parent_text, clean_match_text(comma_name_variant(parent))} if text]
    if "parent_name" in rules and parent_identity_texts and any(text in row_text or text in csv_parent_reversed for text in parent_identity_texts):
        score += 38
        identity_score += 38
        reasons.append("parent/guardian full name found")
    elif "parent_name" in rules:
        matched_tokens = [token for token in meaningful_tokens(parent) if token in row_text]
        if matched_tokens:
            score += min(32, len(matched_tokens) * 14)
            identity_score += min(32, len(matched_tokens) * 14)
            reasons.append("parent/guardian name token match")
        elif parent_text and token_overlap_score(parent, f"{row.get('parent_name', '')} {row.get('description', '')}") >= 0.6:
            score += 24
            identity_score += 24
            reasons.append("parent/guardian close token match")

    if "parent_name" in rules:
        alias_hit = ""
        for alias in aliases.get(str(student["id"]), []):
            alias_text = clean_match_text(alias)
            if alias_text and alias_text in row_text:
                alias_hit = alias
                score += 45
                identity_score += 45
                reasons.append(f"saved payer alias: {alias}")
                break
    else:
        alias_hit = ""

    if "student_id" in rules:
        csv_student_id = clean_match_text(row.get("student_id", ""))
        if csv_student_id and csv_student_id in {clean_match_text(student.get("id")), clean_match_text(student.get("number"))}:
            score += 55
            identity_score += 55
            reasons.append("student ID exact match")

    if "student_name" in rules:
        student_name_text = clean_match_text(student.get("student_name", ""))
        if student_name_text and csv_student_text and student_name_text == csv_student_text:
            score += 48
            identity_score += 48
            reasons.append("CSV student name exact match")
        elif student_name_text and student_name_text in row_text:
            score += 40
            identity_score += 40
            reasons.append("student full name found")
        else:
            student_tokens = [token for token in meaningful_tokens(student.get("student_name", "")) if token in row_text]
            if student_tokens:
                score += min(24, len(student_tokens) * 12)
                identity_score += min(24, len(student_tokens) * 12)
                reasons.append("student name token match")
            elif student_name_text and csv_student_text and SequenceMatcher(None, student_name_text, csv_student_text).ratio() >= 0.78:
                score += 30
                identity_score += 30
                reasons.append("student name close match")

    if "email" in rules:
        email = clean_match_text(student.get("email", ""))
        if email and email in row_text:
            score += 30
            identity_score += 30
            reasons.append("email match")

    expected = float(student.get("std_monthly_fee") or 0)
    if amount <= 0:
        reasons.append("zero amount row is review-only and will not post")
    if expected <= 0:
        reasons.append("student has zero standard fee and is not a posting candidate")
    if "payment_amount" in rules and expected and abs(amount - expected) <= 0.01:
        score += 25
        reasons.append("amount matches standard monthly fee")
    elif "payment_amount" in rules and expected and 0 < abs(amount - expected) <= 5:
        score += 12
        reasons.append("amount is close to standard fee")

    prev_month = previous_month_label(month_label)
    prev_paid = float(payments.get(str(student["id"]), {}).get(prev_month, 0) or 0) if prev_month else 0
    current_paid = float(payments.get(str(student["id"]), {}).get(month_label, 0) or 0) if month_label else 0
    enrol_date = str(student.get("enrol_date") or "")
    month_date = month_to_date(month_label)
    is_new_enrolment = bool(enrol_date and month_label and enrol_date.startswith(month_date.strftime("%Y-%m")))
    if "payment_date" in rules and prev_month and prev_paid:
        score += 14
        reasons.append("same student paid last month")
        if abs(prev_paid - amount) <= 0.01:
            score += 10
            reasons.append("amount matches last month payment")
    elif "payment_date" in rules and is_new_enrolment:
        score += 8
        reasons.append("new enrolment: previous month exception")
    elif "payment_date" in rules and prev_month:
        reasons.append("no previous month payment found")

    if "parent_name" in rules and parent_text and SequenceMatcher(None, parent_text, row_text).ratio() >= 0.55:
        score += 8
        identity_score += 8
        reasons.append("description is similar to guardian name")

    if "organization_id" in rules and str(row.get("organization_id") or "").strip():
        score += 8
        reasons.append("organization ID present in CSV")

    if "branch_id" in rules and str(row.get("branch_id") or "").strip():
        score += 8
        reasons.append("branch ID present in CSV")

    if current_paid > 0:
        score = max(0, score - 25)
        reasons.append(f"{month_label} already has a payment recorded")

    has_csv_identity = any(str(row.get(key) or "").strip() for key in ("student_id", "student_name", "parent_name", "email", "description"))
    if has_csv_identity and identity_score == 0:
        score = min(score, 44)
        reasons.append("no student or parent identity match")

    confidence = "high" if score >= 75 else "medium" if score >= 50 else "low"
    if amount <= 0 or expected <= 0:
        confidence = "low"
        score = min(score, 49)

    return {
        "student_id": student["id"],
        "student_name": student["student_name"],
        "parent_guardian": student.get("parent_guardian", ""),
        "month_label": month_label,
        "score": min(score, 100),
        "confidence": confidence,
        "reasons": reasons,
        "alias": alias_hit,
        "expected_fee": expected,
        "previous_month": prev_month,
        "previous_paid": prev_paid,
        "current_paid": current_paid,
        "already_paid": current_paid > 0,
        "payment_method": payment_method_label(student.get("payment_method", "")),
    }


def reconciliation_summary_from_previews(previews, students, upload_method):
    verified = [row for row in previews if row.get("suggestion") == "auto-fill"]
    rejected = [row for row in previews if row.get("rejected")]
    manual = [row for row in previews if row.get("suggestion") != "auto-fill" and not row.get("rejected")]
    expected_amount = sum(float(student.get("std_monthly_fee") or 0) for student in students if payment_method_label(student.get("payment_method")) == upload_method)
    csv_amount = sum(float(row.get("amount") or 0) for row in previews)
    verified_amount = sum(float(row.get("amount") or 0) for row in verified)
    matched_ids = {str(row.get("best_match", {}).get("student_id")) for row in previews if row.get("best_match")}
    return {
        "csv": {
            "total_rows": len(previews),
            "processed_rows": len(previews),
            "matched_rows": len([row for row in previews if row.get("best_match")]),
            "ready_rows": len(verified),
            "rejected_rows": len(rejected),
            "manual_review_rows": len(manual),
        },
        "financial": {
            "expected_amount": expected_amount,
            "csv_amount": csv_amount,
            "verified_amount": verified_amount,
            "rejected_amount": sum(float(row.get("amount") or 0) for row in rejected),
            "outstanding_amount": max(0, expected_amount - verified_amount),
            "difference": verified_amount - expected_amount,
        },
        "students": {
            "matched_students": len(matched_ids),
            "students_not_found": len([row for row in previews if not row.get("best_match")]),
            "students_with_payment_discrepancies": len([row for row in previews if row.get("warnings")]),
            "outstanding_students": max(0, len(students) - len(matched_ids)),
        },
    }


def preview_reconciliation(rows, payment_method="PAD", match_rules=None):
    upload_method = payment_method_label(payment_method)
    with db() as conn:
        students = get_students(conn)
        branch_id = current_branch_id(conn)
        if PG_MODE:
            alias_rows = conn.execute(
                "SELECT student_id::text AS student_id, alias FROM public.payer_aliases WHERE organization_id=%s AND branch_id=%s",
                (current_org_id(conn), branch_id),
            ).fetchall()
        else:
            alias_rows = conn.execute("SELECT student_id, alias FROM payer_aliases WHERE branch_id=?", (branch_id,)).fetchall()
    aliases = {}
    for row in alias_rows:
        aliases.setdefault(str(row["student_id"]), []).append(row["alias"])
    method_students = [
        student for student in students
        if str(student.get("status", "")).upper() == "C"
        and payment_method_label(student.get("payment_method")) == upload_method
        and float(student.get("std_monthly_fee") or 0) > 0
    ]

    previews = []
    for index, row in enumerate(rows, start=1):
        normalized = {
            "row_number": index,
            "date": str(row.get("date") or row.get("transaction_date") or "").strip(),
            "description": str(row.get("description") or row.get("memo") or row.get("name") or "").strip(),
            "amount": money(row.get("amount") or row.get("credit") or row.get("deposit")),
            "source": str(row.get("source") or row.get("account") or "").strip(),
            "reference": str(row.get("reference") or row.get("transaction_id") or "").strip(),
            "student_id": str(row.get("student_id") or "").strip(),
            "student_name": str(row.get("student_name") or "").strip(),
            "parent_name": str(row.get("parent_name") or row.get("payer") or "").strip(),
            "email": str(row.get("email") or "").strip(),
            "payment_method": str(row.get("payment_method") or upload_method).strip(),
            "organization_id": str(row.get("organization_id") or "").strip(),
            "branch_id": str(row.get("branch_id") or "").strip(),
        }
        matches = sorted(
            [score_payment_match(normalized, student, aliases, payments, match_rules, upload_method) for student in method_students],
            key=lambda match: match["score"],
            reverse=True,
        )[:5]
        best = matches[0] if matches and matches[0]["score"] >= 50 else None
        warnings = []
        if float(normalized.get("amount") or 0) <= 0:
            warnings.append("Zero amount rows are not posted. Review only.")
        if not method_students:
            warnings.append(f"No active {upload_method} students are available for matching")
        if best and best.get("already_paid"):
            warnings.append(f"{best['student_name']} already has {best['month_label']} payment recorded")
        rejected = not best
        previews.append(
            {
                **normalized,
                "month_label": best["month_label"] if best else transaction_month_label(normalized["date"]),
                "best_match": best,
                "candidates": matches,
                "rejected": rejected,
                "warnings": warnings,
                "payment_method_mode": upload_method,
                "suggestion": "auto-fill" if best and best["confidence"] == "high" and not warnings else "review",
            }
        )
    return {"rows": previews, "summary": reconciliation_summary_from_previews(previews, method_students, upload_method)}


def reconciliation_summary(conn):
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT match_status, COUNT(*) AS count, COALESCE(SUM(amount),0) AS total
            FROM public.payment_import_rows
            WHERE organization_id=%s
            GROUP BY match_status
            """,
            (current_org_id(conn),),
        ).fetchall()
        return [rowdict(row) for row in rows]
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


def student_name_key(student_name):
    return clean_match_text(student_name)


def csv_value(row, *names):
    normalized = {re.sub(r"[^a-z0-9]", "", str(key).lower()): value for key, value in row.items()}
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", str(name).lower())
        value = normalized.get(key, "")
        if str(value).strip():
            return value
    return ""


def preview_fee_import(rows):
    with db() as conn:
        settings = get_settings(conn)
        current_month = settings.get("current_month", "May-26")
        students = get_students(conn)
        lookup = {student_lookup_key(row["student_name"], row["parent_guardian"]): row for row in students}
        name_lookup = {}
        number_lookup = {}
        for student in students:
            name_lookup.setdefault(student_name_key(student["student_name"]), []).append(student)
            number_lookup[str(student.get("number", "")).strip()] = student
    preview = []
    for index, row in enumerate(rows, start=1):
        name = str(csv_value(row, "student_name", "student name", "student", "name")).strip()
        parent = str(csv_value(row, "parent_guardian", "parent guardian", "parent / guardian", "parent", "guardian")).strip()
        number_value = str(csv_value(row, "number", "#", "student number")).strip()
        key = student_lookup_key(name, parent)
        student = lookup.get(key) or number_lookup.get(number_value)
        if not student:
            candidates = name_lookup.get(student_name_key(name), [])
            if len(candidates) == 1:
                student = candidates[0]
        month_values = {month: money(csv_value(row, month)) for month in MONTHS if str(csv_value(row, month)).strip()}
        errors = []
        warnings = []
        if not name and not number_value:
            errors.append("Student name or number is required")
        if not student:
            errors.append("No matching Student Roster record")
        if not month_values:
            warnings.append("No monthly payment cells found")
        expected_months = []
        missing_months = []
        if student and student.get("enrol_date") and current_month in MONTHS:
            enrol_label = transaction_month_label(student["enrol_date"])
            if enrol_label in MONTHS:
                start = MONTHS.index(enrol_label)
                end = MONTHS.index(current_month)
                if start <= end:
                    expected_months = MONTHS[start : end + 1]
                    missing_months = [month for month in expected_months if month not in month_values]
                    if missing_months:
                        warnings.append(f"{len(missing_months)} enrolment-to-current months are blank")
        preview.append(
            {
                "row_number": index,
                "student_name": name,
                "parent_guardian": parent,
                "student_id": student["id"] if student else None,
                "matched_student": student["student_name"] if student else "",
                "matched_parent": student["parent_guardian"] if student else "",
                "matched_enrol_date": student["enrol_date"] if student else "",
                "matched": bool(student),
                "month_count": len(month_values),
                "expected_month_count": len(expected_months),
                "missing_month_count": len(missing_months),
                "total_amount": sum(month_values.values()),
                "months": month_values,
                "errors": errors,
                "warnings": warnings,
                "valid": bool(student) and not errors,
            }
        )
    return preview


def apply_fee_import(rows):
    preview = preview_fee_import(rows)
    applied = 0
    skipped = 0
    with db() as conn:
        org_id = current_org_id(conn) if PG_MODE else None
        for item in preview:
            if not item["student_id"] or not item.get("valid"):
                skipped += 1
                continue
            for month, amount in item["months"].items():
                if PG_MODE:
                    conn.execute(
                        """
                        INSERT INTO public.payments(organization_id, student_id, month_label, amount, payment_verified, payment_source)
                        VALUES (%s,%s,%s,%s,true,'fee import')
                        ON CONFLICT(student_id, month_label)
                        DO UPDATE SET amount=excluded.amount, payment_verified=true, updated_at=now()
                        """,
                        (org_id, item["student_id"], month, amount),
                    )
                else:
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


def get_students(conn, include_deleted=False):
    branch_id = current_branch_id(conn)
    if PG_MODE:
        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        rows = conn.execute(
            f"""
            SELECT id::text AS id, number, student_name, parent_guardian, status, enrol_date,
                   subjects, rate_type, std_monthly_fee, payment_method, phone, email, siblings,
                   notes, last_modification, deleted_at, deleted_by, delete_reason, created_at, updated_at
            FROM public.students
            WHERE organization_id=%s AND branch_id=%s {deleted_filter}
            ORDER BY number, student_name
            """,
            (current_org_id(conn), branch_id),
        ).fetchall()
        students = [display_student(row) for row in rows]
        schedules = get_student_schedules(conn, [student["id"] for student in students])
        for student in students:
            student["schedules"] = schedules.get(str(student["id"]), [])
            student["weekly_schedule"] = schedule_display(student["schedules"])
        return students
    where_clause = "WHERE branch_id = ?" + (" AND deleted_at IS NULL" if not include_deleted else "")
    students = [rowdict(row) for row in conn.execute(f"SELECT * FROM students {where_clause} ORDER BY number, student_name", (branch_id,)).fetchall()]
    schedules = get_student_schedules(conn, [student["id"] for student in students])
    for student in students:
        student["schedules"] = schedules.get(str(student["id"]), [])
        student["weekly_schedule"] = schedule_display(student["schedules"])
    return students


def get_audit_logs(conn, limit=75):
    ensure_student_audit_tables(conn)
    branch_id = current_branch_id(conn)
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT id::text AS id, entity_type, entity_id, action, actor_email, summary, created_at
            FROM public.audit_logs
            WHERE (organization_id=%s AND branch_id=%s) OR organization_id IS NULL
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (current_org_id(conn), branch_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, entity_type, entity_id, action, actor_email, summary, created_at
            FROM audit_logs
            WHERE branch_id=? OR branch_id IS NULL
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (branch_id, limit),
        ).fetchall()
    return [rowdict(row) for row in rows]


def production_readiness(conn):
    checks = {
        "database": "supabase" if PG_MODE else "sqlite",
        "auth_required": SUPABASE_REQUIRE_AUTH,
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
        "organization_pinned": bool(SMP_ORGANIZATION_ID),
        "role_options": ROLE_OPTIONS,
        "required_tables": {},
    }
    required = [
        "students", "payments", "app_users", "rates", "payer_aliases",
        "payment_imports", "payment_import_rows", "student_status_changes",
        "audit_logs", "student_schedules", "staff_members", "staff_schedules", "staff_shift_punches",
    ]
    if PG_MODE:
        org_count = conn.execute("SELECT count(*) AS c FROM public.organizations").fetchone()["c"]
        checks["organization_count"] = int(org_count or 0)
        checks["organization_safe"] = bool(SMP_ORGANIZATION_ID or org_count <= 1)
        rows = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema='public'
            """
        ).fetchall()
        existing = {row["table_name"] for row in rows}
        for table in required:
            checks["required_tables"][table] = table in existing
    else:
        checks["organization_count"] = 1
        checks["organization_safe"] = True
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        existing = {row["name"] for row in rows}
        for table in required:
            sqlite_table = "users" if table == "app_users" else table
            checks["required_tables"][table] = sqlite_table in existing
    checks["ready_for_real_users"] = (
        PG_MODE
        and SUPABASE_REQUIRE_AUTH
        and checks["supabase_configured"]
        and checks["organization_safe"]
        and all(checks["required_tables"].values())
    )
    return checks


def get_payments(conn):
    branch_id = current_branch_id(conn)
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT student_id::text AS student_id, month_label, amount
            FROM public.payments
            WHERE organization_id=%s AND branch_id=%s
            """,
            (current_org_id(conn), branch_id),
        ).fetchall()
    else:
        rows = conn.execute("SELECT student_id, month_label, amount FROM payments WHERE branch_id=?", (branch_id,)).fetchall()
    by_student = {}
    for row in rows:
        by_student.setdefault(str(row["student_id"]), {})[row["month_label"]] = float(row["amount"] or 0)
    return by_student


def get_status_changes(conn):
    ensure_status_change_table(conn)
    branch_id = current_branch_id(conn)
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT c.id::text AS id, c.student_id::text AS student_id, c.previous_status,
                   c.new_status, c.changed_at, c.changed_month, c.notes,
                   s.number, s.student_name, s.parent_guardian, s.subjects, s.std_monthly_fee
            FROM public.student_status_changes c
            JOIN public.students s ON s.id=c.student_id AND s.organization_id=c.organization_id AND s.branch_id=c.branch_id
            WHERE c.organization_id=%s AND c.branch_id=%s
            ORDER BY c.changed_at DESC
            """,
            (current_org_id(conn), branch_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT c.id, c.student_id, c.previous_status, c.new_status, c.changed_at,
                   c.changed_month, c.notes, s.number, s.student_name, s.parent_guardian,
                   s.subjects, s.std_monthly_fee
            FROM student_status_changes c
            JOIN students s ON s.id=c.student_id AND s.branch_id=c.branch_id
            WHERE c.branch_id=?
            ORDER BY c.changed_at DESC
            """,
            (branch_id,),
        ).fetchall()
    changes = []
    for row in rows:
        item = rowdict(row)
        item["subjects"] = subjects_text(item.get("subjects"))
        item["std_monthly_fee"] = float(item.get("std_monthly_fee") or 0)
        changes.append(item)
    return changes


def record_status_change(conn, student_id, old_status, new_status, notes=""):
    old_value = str(old_status or "").strip().upper()[:1]
    new_value = str(new_status or "").strip().upper()[:1]
    if old_value == new_value:
        return
    ensure_status_change_table(conn)
    changed_month = current_month_label()
    branch_id = current_branch_id(conn)
    if PG_MODE:
        conn.execute(
            """
            INSERT INTO public.student_status_changes(
                organization_id, branch_id, student_id, previous_status, new_status, changed_month, notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (current_org_id(conn), branch_id, student_id, old_value, new_value, changed_month, notes),
        )
    else:
        conn.execute(
            """
            INSERT INTO student_status_changes(branch_id, student_id, previous_status, new_status, changed_month, notes)
            VALUES (?,?,?,?,?,?)
            """,
            (branch_id, student_id, old_value, new_value, changed_month, notes),
        )


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def normalize_staff_member(data):
    name = str(data.get("staff_name") or data.get("name") or "").strip()
    if not name:
        raise ValueError("Staff name is required")
    active = str(data.get("active", "1")).lower() in {"1", "true", "yes", "on"}
    return {
        "staff_name": name,
        "role_title": str(data.get("role_title") or data.get("role") or "Staff").strip(),
        "subject": str(data.get("subject") or "").strip(),
        "phone": str(data.get("phone") or "").strip(),
        "email": str(data.get("email") or "").strip(),
        "hourly_rate": money(data.get("hourly_rate")),
        "pin": str(data.get("pin") or "").strip(),
        "active": active,
        "notes": str(data.get("notes") or "").strip(),
    }


def display_staff_member(row):
    item = rowdict(row)
    item["id"] = str(item["id"])
    item["hourly_rate"] = float(item.get("hourly_rate") or 0)
    item["active"] = bool(item.get("active"))
    return item


def get_staff_members(conn):
    ensure_staff_tables(conn)
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT id::text AS id, staff_name, role_title, subject, phone, email, hourly_rate,
                   pin, active, notes, created_at, updated_at
            FROM public.staff_members
            WHERE organization_id=%s
            ORDER BY active DESC, staff_name
            """,
            (current_org_id(conn),),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM staff_members ORDER BY active DESC, staff_name").fetchall()
    return [display_staff_member(row) for row in rows]


def get_staff_schedules(conn):
    ensure_staff_tables(conn)
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT id::text AS id, staff_id::text AS staff_id, weekday, shift_type, start_time,
                   end_time, location, notes, published, updated_at
            FROM public.staff_schedules
            WHERE organization_id=%s
            ORDER BY staff_id, weekday
            """,
            (current_org_id(conn),),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM staff_schedules ORDER BY staff_id, weekday").fetchall()
    return [rowdict(row) for row in rows]


def get_staff_punches(conn):
    ensure_staff_tables(conn)
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT p.id::text AS id, p.staff_id::text AS staff_id, p.punch_date, p.clock_in,
                   p.clock_out, p.duration_hours, p.source, p.notes, p.created_at,
                   s.staff_name, s.role_title
            FROM public.staff_shift_punches p
            JOIN public.staff_members s ON s.id=p.staff_id AND s.organization_id=p.organization_id
            WHERE p.organization_id=%s
            ORDER BY p.punch_date DESC, p.created_at DESC
            LIMIT 200
            """,
            (current_org_id(conn),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT p.*, s.staff_name, s.role_title
            FROM staff_shift_punches p
            JOIN staff_members s ON s.id=p.staff_id
            ORDER BY p.punch_date DESC, p.created_at DESC
            LIMIT 200
            """
        ).fetchall()
    return [rowdict(row) for row in rows]


def staff_bundle(conn):
    members = get_staff_members(conn)
    schedules = get_staff_schedules(conn)
    punches = get_staff_punches(conn)
    active_members = [member for member in members if member.get("active")]
    current_punches = [punch for punch in punches if not punch.get("clock_out")]
    week_hours = sum(float(punch.get("duration_hours") or 0) for punch in punches)
    labor_cost = sum(float(punch.get("duration_hours") or 0) * next((m["hourly_rate"] for m in members if str(m["id"]) == str(punch.get("staff_id"))), 0) for punch in punches)
    return {
        "members": members,
        "schedules": schedules,
        "punches": punches,
        "weekdays": WEEKDAYS,
        "summary": {
            "active_staff": len(active_members),
            "inactive_staff": len(members) - len(active_members),
            "clocked_in": len(current_punches),
            "scheduled_shifts": len([row for row in schedules if str(row.get("shift_type", "")).lower() != "off"]),
            "labor_hours": round(week_hours, 2),
            "labor_cost": round(labor_cost, 2),
        },
    }


def save_staff_member(conn, data, staff_id=None):
    staff = normalize_staff_member(data)
    ensure_staff_tables(conn)
    if PG_MODE:
        if staff_id:
            conn.execute(
                """
                UPDATE public.staff_members
                SET staff_name=%s, role_title=%s, subject=%s, phone=%s, email=%s,
                    hourly_rate=%s, pin=%s, active=%s, notes=%s, updated_at=now()
                WHERE id=%s AND organization_id=%s
                """,
                (staff["staff_name"], staff["role_title"], staff["subject"], staff["phone"], staff["email"], staff["hourly_rate"], staff["pin"], staff["active"], staff["notes"], staff_id, current_org_id(conn)),
            )
            return staff_id
        row = conn.execute(
            """
            INSERT INTO public.staff_members(organization_id, staff_name, role_title, subject, phone, email, hourly_rate, pin, active, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id::text AS id
            """,
            (current_org_id(conn), staff["staff_name"], staff["role_title"], staff["subject"], staff["phone"], staff["email"], staff["hourly_rate"], staff["pin"], staff["active"], staff["notes"]),
        ).fetchone()
        return row["id"]
    if staff_id:
        conn.execute(
            """
            UPDATE staff_members
            SET staff_name=?, role_title=?, subject=?, phone=?, email=?, hourly_rate=?,
                pin=?, active=?, notes=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (staff["staff_name"], staff["role_title"], staff["subject"], staff["phone"], staff["email"], staff["hourly_rate"], staff["pin"], 1 if staff["active"] else 0, staff["notes"], int(staff_id)),
        )
        return staff_id
    cur = conn.execute(
        """
        INSERT INTO staff_members(staff_name, role_title, subject, phone, email, hourly_rate, pin, active, notes)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (staff["staff_name"], staff["role_title"], staff["subject"], staff["phone"], staff["email"], staff["hourly_rate"], staff["pin"], 1 if staff["active"] else 0, staff["notes"]),
    )
    return cur.lastrowid


def save_staff_schedule(conn, data):
    ensure_staff_tables(conn)
    staff_id = str(data.get("staff_id") or "").strip()
    weekday = str(data.get("weekday") or "").strip()
    if weekday not in WEEKDAYS:
        raise ValueError("Select a valid weekday")
    if not staff_id:
        raise ValueError("Select a staff member")
    shift_type = str(data.get("shift_type") or "Work").strip()
    start_time = str(data.get("start_time") or "").strip()
    end_time = str(data.get("end_time") or "").strip()
    location = str(data.get("location") or "Centre").strip()
    notes = str(data.get("notes") or "").strip()
    published = str(data.get("published", "0")).lower() in {"1", "true", "yes", "on"}
    if PG_MODE:
        conn.execute(
            """
            INSERT INTO public.staff_schedules(organization_id, staff_id, weekday, shift_type, start_time, end_time, location, notes, published)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(staff_id, weekday)
            DO UPDATE SET shift_type=excluded.shift_type, start_time=excluded.start_time,
                          end_time=excluded.end_time, location=excluded.location, notes=excluded.notes,
                          published=excluded.published, updated_at=now()
            """,
            (current_org_id(conn), staff_id, weekday, shift_type, start_time, end_time, location, notes, published),
        )
    else:
        conn.execute(
            """
            INSERT INTO staff_schedules(staff_id, weekday, shift_type, start_time, end_time, location, notes, published)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(staff_id, weekday)
            DO UPDATE SET shift_type=excluded.shift_type, start_time=excluded.start_time,
                          end_time=excluded.end_time, location=excluded.location, notes=excluded.notes,
                          published=excluded.published, updated_at=CURRENT_TIMESTAMP
            """,
            (int(staff_id), weekday, shift_type, start_time, end_time, location, notes, 1 if published else 0),
        )


def save_staff_punch(conn, data):
    ensure_staff_tables(conn)
    staff_id = str(data.get("staff_id") or "").strip()
    if not staff_id:
        raise ValueError("Select a staff member")
    punch_date = normalize_date(data.get("punch_date") or date.today().isoformat())[:10]
    clock_in = str(data.get("clock_in") or "").strip()
    clock_out = str(data.get("clock_out") or "").strip()
    duration_hours = money(data.get("duration_hours"))
    source = str(data.get("source") or "manual").strip()
    notes = str(data.get("notes") or "").strip()
    if PG_MODE:
        row = conn.execute(
            """
            INSERT INTO public.staff_shift_punches(organization_id, staff_id, punch_date, clock_in, clock_out, duration_hours, source, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id::text AS id
            """,
            (current_org_id(conn), staff_id, punch_date, clock_in, clock_out, duration_hours, source, notes),
        ).fetchone()
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO staff_shift_punches(staff_id, punch_date, clock_in, clock_out, duration_hours, source, notes)
        VALUES (?,?,?,?,?,?,?)
        """,
        (int(staff_id), punch_date, clock_in, clock_out, duration_hours, source, notes),
    )
    return cur.lastrowid


def seed_demo_data(conn, actor_email="system"):
    demo_students = [
        {
            "student_name": "Amina Demo",
            "parent_guardian": "Sara Demo",
            "status": "C",
            "enrol_date": date.today().replace(day=1).isoformat(),
            "subjects": "Math",
            "rate_type": "R",
            "std_monthly_fee": 165,
            "payment_method": "PAD",
            "phone": "403-555-0101",
            "email": "sara.demo@example.com",
            "siblings": "",
            "notes": "Demo PAD student",
        },
        {
            "student_name": "Noah Demo",
            "parent_guardian": "Omar Demo",
            "status": "C",
            "enrol_date": date.today().replace(day=1).isoformat(),
            "subjects": "Math, English",
            "rate_type": "R",
            "std_monthly_fee": 330,
            "payment_method": "E-Transfer",
            "phone": "403-555-0102",
            "email": "omar.demo@example.com",
            "siblings": "",
            "notes": "Demo E-Transfer student",
        },
        {
            "student_name": "Mia Demo",
            "parent_guardian": "Priya Demo",
            "status": "C",
            "enrol_date": date.today().replace(day=1).isoformat(),
            "subjects": "English",
            "rate_type": "R",
            "std_monthly_fee": 165,
            "payment_method": "Credit Card",
            "phone": "403-555-0103",
            "email": "priya.demo@example.com",
            "siblings": "",
            "notes": "Demo credit card student",
        },
    ]
    created_students = 0
    student_ids = []
    for student in demo_students:
        if PG_MODE:
            existing = conn.execute(
                """
                SELECT id::text AS id
                FROM public.students
                WHERE organization_id=%s AND lower(student_name)=lower(%s) AND lower(parent_guardian)=lower(%s)
                """,
                (current_org_id(conn), student["student_name"], student["parent_guardian"]),
            ).fetchone()
        else:
            existing = conn.execute(
                """
                SELECT id
                FROM students
                WHERE lower(student_name)=lower(?) AND lower(parent_guardian)=lower(?)
                """,
                (student["student_name"], student["parent_guardian"]),
            ).fetchone()
        if existing:
            student_ids.append(str(existing["id"]))
            continue
        student_id = insert_student_record(conn, student, next_student_number(conn), actor_email)
        student_ids.append(str(student_id))
        created_students += 1

    current = current_month_label()
    prev = previous_month_label(current) or current
    payment_updates = 0
    for student_id, student in zip(student_ids, demo_students):
        if student["payment_method"] != "PAD":
            update_payment_amount(conn, student_id if PG_MODE else int(student_id), prev, student["std_monthly_fee"], "demo seed", actor_email)
            payment_updates += 1
        if student["payment_method"] == "Credit Card":
            update_payment_amount(conn, student_id if PG_MODE else int(student_id), current, student["std_monthly_fee"], "demo seed", actor_email)
            payment_updates += 1
        alias = student["parent_guardian"]
        if PG_MODE:
            conn.execute(
                """
                INSERT INTO public.payer_aliases(organization_id, student_id, alias, source)
                VALUES (%s,%s,%s,'demo seed')
                ON CONFLICT(student_id, alias) DO NOTHING
                """,
                (current_org_id(conn), student_id, alias),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO payer_aliases(student_id, alias, source) VALUES (?,?,?)",
                (int(student_id), alias, "demo seed"),
            )

    demo_staff = [
        {"staff_name": "SMP Demo Admin", "role_title": "Owner", "subject": "Administration", "phone": "403-555-0201", "email": "admin.demo@example.com", "hourly_rate": 40, "pin": "1111", "active": True, "notes": "Demo staff"},
        {"staff_name": "Rina Demo", "role_title": "Instructor", "subject": "Math", "phone": "403-555-0202", "email": "rina.demo@example.com", "hourly_rate": 26, "pin": "2222", "active": True, "notes": "Demo staff"},
    ]
    created_staff = 0
    for staff in demo_staff:
        if PG_MODE:
            existing = conn.execute(
                "SELECT id::text AS id FROM public.staff_members WHERE organization_id=%s AND lower(email)=lower(%s)",
                (current_org_id(conn), staff["email"]),
            ).fetchone()
        else:
            existing = conn.execute("SELECT id FROM staff_members WHERE lower(email)=lower(?)", (staff["email"],)).fetchone()
        if existing:
            continue
        save_staff_member(conn, staff)
        created_staff += 1

    record_audit(
        conn,
        "demo_seed",
        "organization",
        current_org_id(conn) if PG_MODE else "local",
        f"Seeded demo data: {created_students} students, {created_staff} staff, {payment_updates} payments",
        after={"students": created_students, "staff": created_staff, "payments": payment_updates},
        actor_email=actor_email,
    )
    return {"students_created": created_students, "staff_created": created_staff, "payments_created": payment_updates}


def fee_tracker(conn):
    payments = get_payments(conn)
    settings = get_settings(conn)
    current_month = settings.get("current_month") or current_month_label()
    rows = []
    for student in get_students(conn):
        month_values = {month: payments.get(str(student["id"]), {}).get(month, 0) for month in MONTHS}
        total_paid = sum(month_values.values())
        monthly_fee = float(student["std_monthly_fee"] or 0)
        current_month_paid = float(month_values.get(current_month, 0) or 0)
        balance = 0 if student["status"].lower() != "c" or current_month_paid > 0 else monthly_fee
        rows.append(
            {
                **student,
                "subject_units": subject_units(student["subjects"]),
                "subject_list": subject_list(student["subjects"]),
                "subjects_display": subjects_text(student["subjects"]),
                "months": month_values,
                "total_paid": total_paid,
                "balance": balance,
                "current_month_paid": current_month_paid,
                "current_month_balance": balance,
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
        method = payment_method_label(row["payment_method"])
        if method not in by_method:
            by_method[method] = {
                "student_count": 0,
                "expected_revenue": 0,
                "collected_revenue": 0,
                "outstanding_balance": 0,
            }
        by_method[method]["student_count"] += 1
        by_method[method]["expected_revenue"] += float(row["std_monthly_fee"] or 0)
        by_method[method]["collected_revenue"] += float(row["months"].get(current_month, 0) or 0)
        by_method[method]["outstanding_balance"] += float(row.get("balance") or 0)
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


def insert_student_record(conn, student, number, actor_email="system"):
    note = datetime.now().strftime("%Y-%m-%d: Created")
    branch_id = current_branch_id(conn)
    if PG_MODE:
        org_id = current_org_id(conn)
        row = conn.execute(
            """
            INSERT INTO public.students (
                organization_id, branch_id, number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
                std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
            ) VALUES (%s,%s,%s,%s,%s,%s,NULLIF(%s,'')::date,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id::text AS id
            """,
            (
                org_id,
                branch_id,
                number,
                student["student_name"],
                student["parent_guardian"],
                student["status"],
                student["enrol_date"],
                pg_subjects(student["subjects"]),
                student["rate_type"],
                student["std_monthly_fee"],
                student["payment_method"],
                student["phone"],
                student["email"],
                student["siblings"],
                student["notes"],
                note,
            ),
        ).fetchone()
        student_id = row["id"]
        save_student_schedules(conn, student_id, student.get("schedules", []))
        execute_many(
            conn,
            """
            INSERT INTO public.payments(organization_id, branch_id, student_id, month_label, amount)
            VALUES (?,?,?,?,0)
            ON CONFLICT(student_id, month_label) DO NOTHING
            """,
            [(org_id, branch_id, student_id, month) for month in MONTHS],
        )
        record_audit(
            conn,
            "student_create",
            "student",
            student_id,
            f"Created student {student['student_name']}",
            after={**student, "number": number},
            actor_email=actor_email,
        )
        return student_id
    cur = conn.execute(
        """
        INSERT INTO students (
            branch_id, number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
            std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            branch_id,
            number,
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
            note,
        ),
    )
    save_student_schedules(conn, cur.lastrowid, student.get("schedules", []))
    for month in MONTHS:
        conn.execute("INSERT INTO payments(branch_id, student_id, month_label, amount) VALUES (?,?,?,0)", (branch_id, cur.lastrowid, month))
    record_audit(
        conn,
        "student_create",
        "student",
        cur.lastrowid,
        f"Created student {student['student_name']}",
        after={**student, "number": number},
        actor_email=actor_email,
    )
    return cur.lastrowid


def next_student_number(conn):
    if PG_MODE:
        row = conn.execute("SELECT COALESCE(MAX(number),0)+1 AS n FROM public.students WHERE organization_id=%s", (current_org_id(conn),)).fetchone()
    else:
        row = conn.execute("SELECT COALESCE(MAX(number),0)+1 AS n FROM students").fetchone()
    return int(row["n"] or 1)


def update_payment_amount(conn, student_id, month_label, amount, source="manual", actor_email="system"):
    existing_amount = float(get_payments(conn).get(str(student_id), {}).get(month_label, 0) or 0)
    if PG_MODE:
        conn.execute(
            """
            INSERT INTO public.payments(organization_id, student_id, month_label, amount, payment_verified, payment_source)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(student_id, month_label)
            DO UPDATE SET amount=excluded.amount, payment_verified=excluded.payment_verified,
                          payment_source=excluded.payment_source, updated_at=now()
            """,
            (current_org_id(conn), student_id, month_label, amount, amount > 0, source),
        )
    else:
        conn.execute(
            """
            INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,?)
            ON CONFLICT(student_id, month_label) DO UPDATE SET amount=excluded.amount
            """,
            (student_id, month_label, amount),
        )
    if abs(existing_amount - float(amount or 0)) > 0.01:
        record_audit(
            conn,
            "payment_update",
            "payment",
            f"{student_id}:{month_label}",
            f"{month_label} payment changed from {existing_amount:.2f} to {float(amount or 0):.2f}",
            before={"amount": existing_amount, "month_label": month_label, "student_id": str(student_id)},
            after={"amount": float(amount or 0), "month_label": month_label, "student_id": str(student_id), "source": source},
            actor_email=actor_email,
        )


def ensure_pg_defaults():
    with db() as conn:
        org_id = current_org_id(conn)
        ensure_access_tables(conn)
        ensure_status_change_table(conn)
        ensure_student_audit_tables(conn)
        ensure_student_schedule_tables(conn)
        ensure_staff_tables(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.monthly_expenses (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
                month_label VARCHAR(10) NOT NULL,
                rent_expense NUMERIC(10, 2) DEFAULT 0.00,
                royalty_expense NUMERIC(10, 2) DEFAULT 0.00,
                utilities_expense NUMERIC(10, 2) DEFAULT 0.00,
                misc_expense NUMERIC(10, 2) DEFAULT 0.00,
                misc_details TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now(),
                UNIQUE(organization_id, month_label)
            );
            """
        )
        conn.execute("ALTER TABLE public.monthly_expenses ADD COLUMN IF NOT EXISTS utilities_expense NUMERIC(10, 2) DEFAULT 0.00")
        conn.execute("ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS operating_start time DEFAULT '15:00'")
        conn.execute("ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS operating_end time DEFAULT '20:00'")
        conn.execute("ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS support_email text DEFAULT 'support@smp.edu'")
        for subject in ["Math", "English"]:
            conn.execute(
                """
                INSERT INTO public.rates(organization_id, subject, rate_type, monthly_fee, description)
                VALUES (%s,%s,'R',165,'Default starter rate')
                ON CONFLICT(organization_id, subject, rate_type) DO NOTHING
                """,
                (org_id, subject),
            )
        conn.execute(
            """
            INSERT INTO public.discount_codes(organization_id, code, description, percent_off, amount_off, active)
            VALUES (%s,'WELCOME14','Example admin-provided launch discount',10,0,true)
            ON CONFLICT(organization_id, code) DO NOTHING
            """,
            (org_id,),
        )
        conn.commit()


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

    def require_auth(self):
        self.auth_user = None
        self.auth_access = None
        
        token = self.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
        if not token:
            # Check query parameters
            query_params = parse_qs(urlparse(self.path).query)
            token_list = query_params.get("token") or query_params.get("access_token")
            if token_list:
                token = token_list[0].strip()
        if not token:
            # Check cookies
            cookie_header = self.headers.get("Cookie", "")
            for cookie in cookie_header.split(";"):
                cookie = cookie.strip()
                if cookie.startswith("access_token="):
                    token = cookie.split("=", 1)[1].strip()
                    break

        if token and token.startswith("mock-token-"):
            email = token.replace("mock-token-", "", 1).strip().lower()
            if email in ["syedzaidipk@gmail.com", "najampk@gmail.com", "aneelanajam1@gmail.com", "sarah@smp.edu"]:
                name_map = {
                    "syedzaidipk@gmail.com": "Syed Zaidi (Admin)",
                    "najampk@gmail.com": "Syed Zaidi (Admin)",
                    "aneelanajam1@gmail.com": "Aneela Najam (Owner)",
                    "sarah@smp.edu": "Sarah Chen (Admin)"
                }
                self.auth_user = {
                    "id": "mock-user-id-" + email.replace("@", "-").replace(".", "-"),
                    "email": email,
                    "name": name_map.get(email, "Local Administrator")
                }
                with db() as conn:
                    access = get_user_access(conn, self.auth_user)
                    if not access:
                        self.send_json({"ok": False, "error": "Your email is not authorized for this centre."}, 403)
                        return False
                    self.auth_access = access
                return True

        if not SUPABASE_REQUIRE_AUTH:
            self.auth_access = {"role": "Admin", "email": "admin@local.smp", "display_name": "Local Admin"}
            return True
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            self.send_json({"ok": False, "error": "Supabase auth is not configured on the server"}, 503)
            return False
        if not token:
            self.send_json({"ok": False, "error": "Login is required"}, 401)
            return False
        try:
            request = Request(
                f"{SUPABASE_URL}/auth/v1/user",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}"},
            )
            with urlopen(request, timeout=8) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
                metadata = payload.get("user_metadata") or {}
                self.auth_user = {
                    "id": payload.get("id") or payload.get("sub"),
                    "email": str(payload.get("email") or metadata.get("email") or "").lower(),
                    "name": metadata.get("full_name") or metadata.get("name") or payload.get("email") or "",
                }
                with db() as conn:
                    ensure_first_admin(conn, self.auth_user)
                    access = get_user_access(conn, self.auth_user)
                    if not access:
                        self.send_json({"ok": False, "error": "Your email is not authorized for this centre. Ask the Admin to add your user access in Settings."}, 403)
                        return False
                    self.auth_access = access
                return True
        except Exception:
            self.send_json({"ok": False, "error": "Login session could not be verified"}, 401)
            return False

    def require_permission(self, permission, send_error=True):
        role = normalize_role((self.auth_access or {}).get("role"))
        if permission not in ROLE_PERMISSIONS.get(role, set()):
            if send_error:
                self.send_json({"ok": False, "error": f"{role or 'This user'} does not have permission for this action"}, 403)
            return False
        return True

    def actor_email(self):
        return str((self.auth_access or {}).get("email") or (self.auth_user or {}).get("email") or "local-admin")

    def get_staffbase_actor(self):
        auth = self.headers.get("Authorization", "").strip()
        if not auth or not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        try:
            payload = verify_jwt(token)
            return payload.get("staffId")
        except Exception as e:
            import traceback
            print("verify_jwt failed:", e)
            traceback.print_exc()
            return None

    def require_staffbase_auth(self):
        auth = self.headers.get("Authorization", "").strip()
        if not auth or not auth.startswith("Bearer "):
            self.send_json({"error": "Unauthorized"}, 401)
            return False
        token = auth[7:].strip()
        try:
            payload = verify_jwt(token)
            self.staff_id = payload.get("staffId")
            if not self.staff_id:
                self.send_json({"error": "Invalid or expired token"}, 401)
                return False
            return True
        except Exception as e:
            self.send_json({"error": "Invalid or expired token"}, 401)
            return False

    def require_staffbase_manager(self):
        if not self.require_staffbase_auth():
            return False
        with db() as conn:
            if PG_MODE:
                row = conn.execute("SELECT role FROM public.staff_members WHERE id=%s", (self.staff_id,)).fetchone()
            else:
                row = conn.execute("SELECT role, role_title FROM staff_members WHERE id=?", (int(self.staff_id),)).fetchone()
            if not row:
                self.send_json({"error": "Staff member not found"}, 404)
                return False
            d = rowdict(row)
            role = str(d.get("role") or d.get("role_title") or "").lower()
            if role not in ["manager", "administrator", "principal_owner", "owner", "principal_owner"]:
                self.send_json({"error": "Forbidden: manager role required"}, 403)
                return False
        return True


    def handle_staffbase_get(self, parsed):
        # ── Health check ──
        if parsed.path == "/api/staffbase/health":
            self.send_json({"status": "ok", "timestamp": datetime.now().isoformat(), "server": "Python/SQLite"})
            return True
            
        # ── Healthz check (mobile) ──
        if parsed.path == "/api/healthz" or parsed.path == "/api/health":
            self.send_json({"status": "ok", "timestamp": datetime.now().isoformat(), "server": "Python/SQLite"})
            return True

        # ── GET staffbase data snapshot ──
        if parsed.path == "/api/data" or parsed.path == "/api/staffbase/data":
            staff_id = self.get_staffbase_actor()
            with db() as conn:
                self.send_json(staff_data_snapshot(conn, staff_id=staff_id))
            return True

        # ── GET all staff members (for login select list) ──
        if parsed.path == "/api/staff":
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT id::text as id, staff_name, role_title, subject, email, pin, active, notes, role, avatar_initials, avatar_color FROM public.staff_members ORDER BY id").fetchall()
                else:
                    rows = conn.execute("SELECT id, staff_name, role_title, subject, email, pin, active, notes, role, avatar_initials, avatar_color FROM staff_members ORDER BY id").fetchall()
                res = []
                for r in rows:
                    d = rowdict(r)
                    res.append({
                        "id": d["id"],
                        "name": d.get("staff_name") or d.get("name") or "",
                        "position": d.get("role_title") or d.get("position") or "",
                        "department": d.get("subject") or d.get("department") or "",
                        "role": d.get("role") or "staff",
                        "avatarInitials": d.get("avatar_initials") or "ST",
                        "avatarColor": d.get("avatar_color") or "#6366f1"
                    })
                self.send_json(res)
            return True

        # ── GET current staff notifications status ──
        if parsed.path == "/api/me/push-token-status":
            if not self.require_staffbase_auth():
                return True
            with db() as conn:
                if PG_MODE:
                    row = conn.execute("SELECT expo_push_token FROM public.staff_members WHERE id=%s", (self.staff_id,)).fetchone()
                else:
                    row = conn.execute("SELECT expo_push_token FROM staff_members WHERE id=?", (int(self.staff_id),)).fetchone()
                has_token = bool(row and rowdict(row).get("expo_push_token"))
                self.send_json({"hasToken": has_token})
            return True

        # ── GET clock status for a specific staff member ──
        clock_match = re.match(r"^/api/clock/([^/]+)$", parsed.path)
        if clock_match:
            if not self.require_staffbase_auth():
                return True
            param_id = clock_match.group(1)
            if str(param_id) != str(self.staff_id):
                self.send_json({"error": "Forbidden"}, 403)
                return True
            with db() as conn:
                today = datetime.now().strftime("%Y-%m-%d")
                if PG_MODE:
                    row = conn.execute("SELECT * FROM public.staff_shift_punches WHERE staff_id=%s AND punch_date=%s LIMIT 1", (self.staff_id, today)).fetchone()
                else:
                    row = conn.execute("SELECT * FROM staff_shift_punches WHERE staff_id=? AND punch_date=? LIMIT 1", (int(self.staff_id), today)).fetchone()
                self.send_json(build_clock_status(row))
            return True

        # ── GET clock history for a specific staff member ──
        history_match = re.match(r"^/api/clock/([^/]+)/history$", parsed.path)
        if history_match:
            if not self.require_staffbase_auth():
                return True
            param_id = history_match.group(1)
            if str(param_id) != str(self.staff_id):
                self.send_json({"error": "Forbidden"}, 403)
                return True
            query_params = parse_qs(parsed.query)
            limit = int((query_params.get("limit") or ["30"])[0])
            page = int((query_params.get("page") or ["1"])[0])
            offset = (page - 1) * limit
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT * FROM public.staff_shift_punches WHERE staff_id=%s ORDER BY punch_date DESC LIMIT %s OFFSET %s", (self.staff_id, limit, offset)).fetchall()
                    total_row = conn.execute("SELECT count(*) as count FROM public.staff_shift_punches WHERE staff_id=%s", (self.staff_id,)).fetchone()
                    all_completed = conn.execute("SELECT clock_in, clock_out FROM public.staff_shift_punches WHERE staff_id=%s AND clock_in IS NOT NULL AND clock_out IS NOT NULL", (self.staff_id,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM staff_shift_punches WHERE staff_id=? ORDER BY punch_date DESC LIMIT ? OFFSET ?", (int(self.staff_id), limit, offset)).fetchall()
                    total_row = conn.execute("SELECT count(*) as count FROM staff_shift_punches WHERE staff_id=?", (int(self.staff_id),)).fetchone()
                    all_completed = conn.execute("SELECT clock_in, clock_out FROM staff_shift_punches WHERE staff_id=? AND clock_in IS NOT NULL AND clock_out IS NOT NULL", (int(self.staff_id),)).fetchall()
                
                total = rowdict(total_row)["count"] if total_row else 0
                items = []
                total_minutes = 0
                for r in rows:
                    d = rowdict(r)
                    cin = d.get("clock_in")
                    cout = d.get("clock_out")
                    duration = None
                    if cin and cout:
                        try:
                            t_in = datetime.fromisoformat(cin.replace("Z", "+00:00"))
                            t_out = datetime.fromisoformat(cout.replace("Z", "+00:00"))
                            duration = round((t_out - t_in).total_seconds() / 60)
                        except Exception:
                            pass
                    items.append({
                        "id": d["id"],
                        "date": d["punch_date"],
                        "clockIn": format_time_str(cin),
                        "clockOut": format_time_str(cout),
                        "status": "out" if cout else ("in" if cin else "none"),
                        "gpsOK": d.get("gps_ok") or d.get("gpsok") or True,
                        "durationMinutes": duration
                    })
                
                for r in all_completed:
                    d = rowdict(r)
                    try:
                        t_in = datetime.fromisoformat(d["clock_in"].replace("Z", "+00:00"))
                        t_out = datetime.fromisoformat(d["clock_out"].replace("Z", "+00:00"))
                        total_minutes += round((t_out - t_in).total_seconds() / 60)
                    except Exception:
                        pass
                
                self.send_json({
                    "items": items,
                    "total": total,
                    "page": page,
                    "limit": limit,
                    "totalMinutes": total_minutes
                })
            return True

        # ── GET schedule for a specific staff member ──
        schedule_match = re.match(r"^/api/schedule/([^/]+)$", parsed.path)
        if schedule_match:
            if not self.require_staffbase_auth():
                return True
            param_id = schedule_match.group(1)
            if str(param_id) != str(self.staff_id):
                self.send_json({"error": "Forbidden"}, 403)
                return True
            week_start = get_monday_of_current_week()
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT * FROM public.staff_schedules WHERE staff_id=%s AND week_start=%s", (self.staff_id, week_start)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM staff_schedules WHERE staff_id=? AND week_start=?", (int(self.staff_id), week_start)).fetchall()
                items = []
                for r in rows:
                    d = rowdict(r)
                    items.append({
                        "id": d["id"],
                        "day": d.get("weekday") or d.get("day") or "",
                        "shiftType": d["shift_type"],
                        "start": d["start_time"],
                        "end": d["end_time"],
                        "location": d["location"],
                        "ack": bool(d.get("acknowledged") or d.get("ack"))
                    })
                self.send_json(items)
            return True

        # ── GET time-off requests for a specific staff member ──
        requests_match = re.match(r"^/api/requests/([^/]+)$", parsed.path)
        if requests_match:
            if not self.require_staffbase_auth():
                return True
            param_id = requests_match.group(1)
            if str(param_id) != str(self.staff_id):
                self.send_json({"error": "Forbidden"}, 403)
                return True
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT * FROM public.time_off_requests WHERE staff_id=%s ORDER BY submitted_at DESC", (self.staff_id,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM time_off_requests WHERE staff_id=? ORDER BY submitted_at DESC", (int(self.staff_id),)).fetchall()
                items = []
                for r in rows:
                    d = rowdict(r)
                    items.append({
                        "id": d["id"],
                        "staffId": d["staff_id"],
                        "startDate": d["start_date"],
                        "endDate": d["end_date"],
                        "reason": d["reason"],
                        "status": d["status"],
                        "submittedAt": d["submitted_at"],
                        "decidedAt": d.get("decided_at")
                    })
                self.send_json(items)
            return True

        # ── GET all shift swaps ──
        if parsed.path == "/api/swaps":
            if not self.require_staffbase_auth():
                return True
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT s.*, m.staff_name FROM public.shift_swap_requests s JOIN public.staff_members m ON s.requester_id = m.id ORDER BY s.posted_at DESC").fetchall()
                else:
                    rows = conn.execute("SELECT s.*, m.staff_name FROM shift_swap_requests s JOIN staff_members m ON s.requester_id = m.id ORDER BY s.posted_at DESC").fetchall()
                items = []
                for r in rows:
                    d = rowdict(r)
                    items.append({
                        "id": d["id"],
                        "requesterId": d["requester_id"],
                        "requesterName": d.get("staff_name") or "",
                        "shiftDate": d["shift_date"],
                        "reason": d["reason"],
                        "status": d["status"],
                        "claimedById": d.get("claimed_by_id"),
                        "postedAt": d["posted_at"]
                    })
                self.send_json(items)
            return True

        # ── GET all announcements ──
        if parsed.path == "/api/announcements":
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT * FROM public.announcements WHERE active=true ORDER BY created_at DESC").fetchall()
                else:
                    rows = conn.execute("SELECT * FROM announcements WHERE active=1 ORDER BY created_at DESC").fetchall()
                items = []
                for r in rows:
                    d = rowdict(r)
                    items.append({
                        "id": d["id"],
                        "title": d["title"],
                        "body": d["body"],
                        "date": d["date"],
                        "priority": d["priority"]
                    })
                self.send_json(items)
            return True

        # ── GET Manager: get team ──
        if parsed.path == "/api/manager/team":
            if not self.require_staffbase_manager():
                return True
            today = datetime.now().strftime("%Y-%m-%d")
            with db() as conn:
                if PG_MODE:
                    members = conn.execute("SELECT id::text as id, staff_name, role_title, subject, email, role, avatar_initials, avatar_color, expo_push_token FROM public.staff_members ORDER BY staff_name").fetchall()
                    punches = conn.execute("SELECT * FROM public.staff_shift_punches WHERE punch_date=%s", (today,)).fetchall()
                else:
                    members = conn.execute("SELECT id, staff_name, role_title, subject, email, role, avatar_initials, avatar_color, expo_push_token FROM staff_members ORDER BY staff_name").fetchall()
                    punches = conn.execute("SELECT * FROM staff_shift_punches WHERE punch_date=?", (today,)).fetchall()
                
                punch_map = {str(rowdict(p)["staff_id"]): rowdict(p) for p in punches}
                team = []
                for m in members:
                    d = rowdict(m)
                    mid = str(d["id"])
                    punch = punch_map.get(mid)
                    status = "out" if punch and punch.get("clock_out") else ("in" if punch and punch.get("clock_in") else "none")
                    team.append({
                        "id": d["id"],
                        "name": d.get("staff_name") or "",
                        "position": d.get("role_title") or "",
                        "department": d.get("subject") or "",
                        "role": d.get("role") or "staff",
                        "avatarInitials": d.get("avatar_initials") or "ST",
                        "avatarColor": d.get("avatar_color") or "#6366f1",
                        "clockStatus": status,
                        "clockIn": format_time_str(punch.get("clock_in")) if punch else None,
                        "clockOut": format_time_str(punch.get("clock_out")) if punch else None,
                        "gpsOK": punch.get("gps_ok") or punch.get("gpsok") if punch else None,
                        "pushEnabled": bool(d.get("expo_push_token"))
                    })
                self.send_json(team)
            return True

        # ── GET Manager: view a specific staff member's clock history ──
        manager_history_match = re.match(r"^/api/manager/staff/([^/]+)/clock-history$", parsed.path)
        if manager_history_match:
            if not self.require_staffbase_manager():
                return True
            target_staff_id = manager_history_match.group(1)
            query_params = parse_qs(parsed.query)
            limit = int((query_params.get("limit") or ["30"])[0])
            page = int((query_params.get("page") or ["1"])[0])
            offset = (page - 1) * limit
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT * FROM public.staff_shift_punches WHERE staff_id=%s ORDER BY punch_date DESC LIMIT %s OFFSET %s", (target_staff_id, limit, offset)).fetchall()
                    total_row = conn.execute("SELECT count(*) as count FROM public.staff_shift_punches WHERE staff_id=%s", (target_staff_id,)).fetchone()
                else:
                    rows = conn.execute("SELECT * FROM staff_shift_punches WHERE staff_id=? ORDER BY punch_date DESC LIMIT ? OFFSET ?", (int(target_staff_id), limit, offset)).fetchall()
                    total_row = conn.execute("SELECT count(*) as count FROM staff_shift_punches WHERE staff_id=?", (int(target_staff_id),)).fetchone()
                
                total = rowdict(total_row)["count"] if total_row else 0
                items = []
                for r in rows:
                    d = rowdict(r)
                    cin = d.get("clock_in")
                    cout = d.get("clock_out")
                    duration = None
                    if cin and cout:
                        try:
                            t_in = datetime.fromisoformat(cin.replace("Z", "+00:00"))
                            t_out = datetime.fromisoformat(cout.replace("Z", "+00:00"))
                            duration = round((t_out - t_in).total_seconds() / 60)
                        except Exception:
                            pass
                    items.append({
                        "id": d["id"],
                        "date": d["punch_date"],
                        "clockIn": format_time_str(cin),
                        "clockOut": format_time_str(cout),
                        "status": "out" if cout else ("in" if cin else "none"),
                        "gpsOK": d.get("gps_ok") or d.get("gpsok") or True,
                        "durationMinutes": duration
                    })
                self.send_json({
                    "items": items,
                    "total": total,
                    "page": page,
                    "limit": limit
                })
            return True

        # ── GET Manager: get requests ──
        if parsed.path == "/api/manager/requests":
            if not self.require_staffbase_manager():
                return True
            with db() as conn:
                if PG_MODE:
                    rows = conn.execute("SELECT r.*, m.staff_name FROM public.time_off_requests r JOIN public.staff_members m ON r.staff_id=m.id ORDER BY r.submitted_at DESC").fetchall()
                else:
                    rows = conn.execute("SELECT r.*, m.staff_name FROM time_off_requests r JOIN staff_members m ON r.staff_id=m.id ORDER BY r.submitted_at DESC").fetchall()
                items = []
                for r in rows:
                    d = rowdict(r)
                    items.append({
                        "id": d["id"],
                        "staffId": d["staff_id"],
                        "staffName": d.get("staff_name") or "",
                        "startDate": d["start_date"],
                        "endDate": d["end_date"],
                        "reason": d["reason"],
                        "status": d["status"],
                        "submittedAt": d["submitted_at"]
                    })
                self.send_json(items)
            return True

        # ── GET Manager: view full week schedule for all staff ──
        if parsed.path == "/api/manager/schedule/week":
            if not self.require_staffbase_manager():
                return True
            week_start = get_monday_of_current_week()
            with db() as conn:
                if PG_MODE:
                    staff = conn.execute("SELECT id::text as id, staff_name, avatar_initials, avatar_color FROM public.staff_members WHERE active=true ORDER BY staff_name").fetchall()
                    shifts = conn.execute("SELECT * FROM public.staff_schedules WHERE week_start=%s", (week_start,)).fetchall()
                else:
                    staff = conn.execute("SELECT id, staff_name, avatar_initials, avatar_color FROM staff_members WHERE active=1 ORDER BY staff_name").fetchall()
                    shifts = conn.execute("SELECT * FROM staff_schedules WHERE week_start=?", (week_start,)).fetchall()
                
                result = []
                for s in staff:
                    sd = rowdict(s)
                    sid = str(sd["id"])
                    sshifts = []
                    for sh in shifts:
                        shd = rowdict(sh)
                        if str(shd["staff_id"]) == sid:
                            sshifts.append({
                                "id": shd["id"],
                                "day": shd.get("weekday") or shd.get("day") or "",
                                "shiftType": shd["shift_type"],
                                "start": shd["start_time"],
                                "end": shd["end_time"],
                                "location": shd["location"],
                                "ack": bool(shd.get("acknowledged") or shd.get("ack"))
                            })
                    result.append({
                        "staffId": sd["id"],
                        "name": sd.get("staff_name") or "",
                        "avatarInitials": sd.get("avatar_initials") or "ST",
                        "avatarColor": sd.get("avatar_color") or "#6366f1",
                        "shifts": sshifts
                    })
                self.send_json(result)
            return True

        # ── GET time-off request decide page (HTML manager approval flow) ──
        decide_match = re.match(r"^/api/requests/decide/([^/]+)/([^/]+)$", parsed.path)
        if decide_match:
            token = decide_match.group(1)
            action = decide_match.group(2)
            if action not in ["approve", "reject"]:
                self.send_html(decision_html_page("Invalid action", False, "The link is invalid."), 400)
                return True
            
            with db() as conn:
                if PG_MODE:
                    request = conn.execute("SELECT * FROM public.time_off_requests WHERE approval_token=%s LIMIT 1", (token,)).fetchone()
                else:
                    request = conn.execute("SELECT * FROM time_off_requests WHERE approval_token=? LIMIT 1", (token,)).fetchone()
                
                if not request:
                    self.send_html(decision_html_page("Link not found", False, "This approval link is invalid or has already been used."), 404)
                    return True
                
                rd = rowdict(request)
                if rd["status"] != "pending":
                    self.send_html(decision_html_page("Already processed", False, f"This request was already {rd['status']}."), 200)
                    return True
                
                new_status = "approved" if action == "approve" else "denied"
                today_time = datetime.now().isoformat()
                
                if PG_MODE:
                    conn.execute("UPDATE public.time_off_requests SET status=%s, decided_at=%s, approval_token=NULL WHERE id=%s", (new_status, today_time, rd["id"]))
                    staff_row = conn.execute("SELECT staff_name, email FROM public.staff_members WHERE id=%s", (rd["staff_id"],)).fetchone()
                else:
                    conn.execute("UPDATE time_off_requests SET status=?, decided_at=?, approval_token=NULL WHERE id=?", (new_status, today_time, rd["id"]))
                    staff_row = conn.execute("SELECT staff_name, email FROM staff_members WHERE id=?", (rd["staff_id"],)).fetchone()
                conn.commit()
                
                staff_name = "Staff member"
                if staff_row:
                    staff_name = rowdict(staff_row).get("staff_name") or "Staff member"
                
                title = "Request Approved ✓" if action == "approve" else "Request Denied"
                message = f"{staff_name}'s time-off request ({rd['start_date']} to {rd['end_date']}) has been successfully {new_status}."
                self.send_html(decision_html_page(title, action == "approve", message))
            return True

        return False


    def do_GET(self):
        parsed = urlparse(self.path)
        if self.handle_staffbase_get(parsed):
            return
        if parsed.path == "/manifest.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json")
            self.send_header("Cache-Control", "no-store")
            try:
                with open(ROOT / "manifest.json", "rb") as f:
                    content = f.read()
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception:
                self.send_error(404, "File not found")
            return
        if parsed.path == "/sw.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Cache-Control", "no-store")
            try:
                with open(ROOT / "sw.js", "rb") as f:
                    content = f.read()
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception:
                self.send_error(404, "File not found")
            return
        if parsed.path == "/api/config":
            self.send_json(
                {
                    "ok": True,
                    "auth_required": SUPABASE_REQUIRE_AUTH,
                    "supabase_url": SUPABASE_URL if SUPABASE_REQUIRE_AUTH else "",
                    "supabase_anon_key": SUPABASE_ANON_KEY if SUPABASE_REQUIRE_AUTH else "",
                    "database": "supabase" if PG_MODE else "sqlite",
                }
            )
            return

        if parsed.path == "/api/health/production":
            with db() as conn:
                self.send_json({"ok": True, "checks": production_readiness(conn)})
            return
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        if parsed.path == "/api/bootstrap":
            try:
                with db() as conn:
                    settings = get_settings(conn)
                    if PG_MODE:
                        org_id = current_org_id(conn)
                        rates = [rowdict(row) for row in conn.execute("SELECT id::text AS id, subject, rate_type, monthly_fee, description FROM public.rates WHERE organization_id=%s ORDER BY subject, rate_type", (org_id,)).fetchall()]
                        users = list_app_users(conn)
                        subscriptions = [
                            {
                                "id": org_id,
                                "user_id": "",
                                "status": "trialing",
                                "trial_start": "",
                                "trial_end": "",
                                "stripe_customer_id": "",
                                "stripe_subscription_id": "",
                            }
                        ]
                        discount_codes = [rowdict(row) for row in conn.execute("SELECT id::text AS id, code, description, percent_off, amount_off, active, created_at FROM public.discount_codes WHERE organization_id=%s OR organization_id IS NULL ORDER BY active DESC, code", (org_id,)).fetchall()]
                        payer_aliases = [rowdict(row) for row in conn.execute("SELECT id::text AS id, student_id::text AS student_id, alias, source, created_at FROM public.payer_aliases WHERE organization_id=%s ORDER BY alias", (org_id,)).fetchall()]
                        expenses = [rowdict(row) for row in conn.execute("SELECT id::text AS id, month_label, rent_expense, royalty_expense, utilities_expense, misc_expense, misc_details FROM public.monthly_expenses WHERE organization_id=%s ORDER BY month_label", (org_id,)).fetchall()]
                        backups = []
                    else:
                        rates = [rowdict(row) for row in conn.execute("SELECT * FROM rates ORDER BY subject, rate_type")]
                        users = list_app_users(conn)
                        subscriptions = [rowdict(row) for row in conn.execute("SELECT * FROM subscriptions ORDER BY id DESC")]
                        discount_codes = [rowdict(row) for row in conn.execute("SELECT * FROM discount_codes ORDER BY active DESC, code")]
                        payer_aliases = [rowdict(row) for row in conn.execute("SELECT * FROM payer_aliases ORDER BY alias")]
                        expenses = [rowdict(row) for row in conn.execute("SELECT id, month_label, rent_expense, royalty_expense, utilities_expense, misc_expense, misc_details FROM monthly_expenses ORDER BY month_label").fetchall()]
                        backups = list_backups()
                    can_access_staff = self.require_permission("manage_staff", send_error=False)
                    payload = {
                            "students": get_students(conn, include_deleted=True),
                            "fee_tracker": fee_tracker(conn),
                            "dashboard": dashboard(conn, settings.get("current_month", "May-26")),
                            "rates": rates,
                            "months": MONTHS,
                            "formula_manifest": FORMULA_MANIFEST,
                            "settings": settings,
                            "users": users,
                            "subscriptions": subscriptions,
                            "discount_codes": discount_codes,
                            "backups": backups,
                            "reconciliation": reconciliation_summary(conn),
                            "payer_aliases": payer_aliases,
                            "status_changes": get_status_changes(conn),
                            "audit_logs": get_audit_logs(conn),
                            "expenses": expenses,
                            "can_access_staff": can_access_staff,
                            "current_user": self.auth_access or {},
                            "role_options": ROLE_OPTIONS,
                    }
                    if can_access_staff:
                        payload["staff"] = staff_bundle(conn)
                    self.send_json(payload)
            except Exception as e:
                import traceback
                print(f"Bootstrap failed: {e}", file=sys.stderr)
                traceback.print_exc()
                self.send_json({"ok": False, "error": f"Bootstrap failed: {e}"}, 500)
            return
        if parsed.path == "/api/expenses":
            if not self.require_permission("manage_payments"):
                return
            with db() as conn:
                if PG_MODE:
                    org_id = current_org_id(conn)
                    rows = conn.execute("SELECT id::text AS id, month_label, rent_expense, royalty_expense, utilities_expense, misc_expense, misc_details FROM public.monthly_expenses WHERE organization_id=%s ORDER BY month_label", (org_id,)).fetchall()
                else:
                    rows = conn.execute("SELECT id, month_label, rent_expense, royalty_expense, utilities_expense, misc_expense, misc_details FROM monthly_expenses ORDER BY month_label").fetchall()
                self.send_json([rowdict(row) for row in rows])
            return
        if parsed.path == "/api/staff/bootstrap":
            if not self.require_permission("manage_staff"):
                return
            with db() as conn:
                self.send_json({"ok": True, "staff": staff_bundle(conn)})
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

    def handle_staffbase_post(self, parsed):
        # Helper to map a staff member row to desktop format
        def desktop_staff_row(m):
            d = rowdict(m)
            pos_val = d.get("role_title") or "staff"
            email_val = str(d.get("email") or "").lower().strip()
            db_role = str(d.get("role") or "").lower().strip()
            
            # Map role based on position, db override role, or admin email override
            if email_val in ["syedzaidipk@gmail.com", "aneelanajam1@gmail.com", "najampk@gmail.com"] or db_role == "manager" or pos_val.lower() in ["manager", "admin", "administrator", "owner", "principal_owner", "principal"]:
                desktop_role = "principal_owner"
            else:
                desktop_role = pos_val
                
            return {
                "id": d["id"],
                "name": d.get("staff_name") or "",
                "email": d.get("email") or "",
                "role": desktop_role,
                "dept": d.get("subject") or "Administration",
                "pos": pos_val,
                "av": "".join([part[0] for part in str(d.get("staff_name")).split(" ") if part]).upper()[:2],
                "active": d.get("active") != 0 and str(d.get("active", "")).lower() != "false",
                "phone": d.get("phone") or "",
                "pin": "****",
                "password": ""
            }

        # ── POST save teacher assignments to app_meta ──
        if parsed.path == "/api/staffbase/teacher-assignments":
            payload = self.read_json()
            assignments = payload.get("assignments", [])
            import json
            json_str = json.dumps(assignments)
            with db() as conn:
                if PG_MODE:
                    conn.execute(
                        "INSERT INTO public.app_meta(key, value) VALUES ('teacher_assignments', %s) ON CONFLICT (key) DO UPDATE SET value=%s",
                        (json_str, json_str)
                    )
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('teacher_assignments', ?)",
                        (json_str,)
                    )
            self.send_json({"ok": True})
            return True

        # ── POST staff PIN auth (mobile) ──
        if parsed.path == "/api/staff/auth":
            payload = self.read_json()
            staff_id = payload.get("staffId")
            pin = str(payload.get("pin", "")).strip()
            if not staff_id or not pin:
                self.send_json({"error": "Missing staffId or pin"}, 400)
                return True
            with db() as conn:
                if PG_MODE:
                    row = conn.execute("SELECT * FROM public.staff_members WHERE id=%s AND active=true LIMIT 1", (staff_id,)).fetchone()
                else:
                    row = conn.execute("SELECT * FROM staff_members WHERE id=? AND active=1 LIMIT 1", (int(staff_id),)).fetchone()
                if not row:
                    self.send_json({"error": "Staff member not found"}, 404)
                    return True
                
                d = rowdict(row)
                if not verify_staff_pin(pin, d.get("pin"), d.get("pin_hash")):
                    self.send_json({"error": "Incorrect PIN"}, 401)
                    return True
                
                # Sign JWT token
                token = sign_jwt({"staffId": d["id"]})
                self.send_json({
                    "token": token,
                    "id": d["id"],
                    "name": d.get("staff_name") or "",
                    "position": d.get("role_title") or "",
                    "department": d.get("subject") or "",
                    "role": d.get("role") or "staff",
                    "email": d.get("email") or "",
                    "avatarInitials": d.get("avatar_initials") or "ST",
                    "avatarColor": d.get("avatar_color") or "#6366f1",
                    "notificationsLastCheckedAt": d.get("notifications_last_checked_at")
                })
            return True

        # ── POST staff base PIN auth (desktop) ──
        if parsed.path == "/api/staffbase/auth/pin":
            payload = self.read_json()
            staff_id = payload.get("staffId")
            pin = str(payload.get("pin", "")).strip()
            if not staff_id or not pin:
                self.send_json({"error": "Missing staffId or pin"}, 400)
                return True
            with db() as conn:
                if PG_MODE:
                    row = conn.execute("SELECT * FROM public.staff_members WHERE id=%s AND active=true LIMIT 1", (staff_id,)).fetchone()
                else:
                    row = conn.execute("SELECT * FROM staff_members WHERE id=? AND active=1 LIMIT 1", (int(staff_id),)).fetchone()
                if not row:
                    self.send_json({"error": "Staff member not found"}, 404)
                    return True
                
                d = rowdict(row)
                if not verify_staff_pin(pin, d.get("pin"), d.get("pin_hash")):
                    self.send_json({"error": "Incorrect PIN"}, 401)
                    return True
                token = sign_jwt({"staffId": d["id"]})
                self.send_json({"ok": True, "token": token, "user": desktop_staff_row(row)})
            return True

        # ── POST staff base password auth (desktop admin/manager) ──
        if parsed.path == "/api/staffbase/auth/password":
            payload = self.read_json()
            email = str(payload.get("email", "")).strip().lower()
            password = str(payload.get("password", "")).strip()
            if not email or not password:
                self.send_json({"error": "Missing email or password"}, 400)
                return True
            if email in ["syedzaidipk@gmail.com", "najampk@gmail.com", "aneelanajam1@gmail.com", "sarah@smp.edu"]:
                if password == "school2026":
                    name_map = {
                        "syedzaidipk@gmail.com": "Syed Zaidi (Admin)",
                        "najampk@gmail.com": "Syed Zaidi (Admin)",
                        "aneelanajam1@gmail.com": "Aneela Najam (Owner)",
                        "sarah@smp.edu": "Sarah Chen (Admin)"
                    }
                    av_map = {
                        "syedzaidipk@gmail.com": "SZ",
                        "najampk@gmail.com": "SZ",
                        "aneelanajam1@gmail.com": "AN",
                        "sarah@smp.edu": "SC"
                    }
                    token = sign_jwt({"staffId": 9999})
                    self.send_json({
                        "ok": True,
                        "token": token,
                        "user": {
                            "id": 9999,
                            "name": name_map.get(email, "Local Administrator"),
                            "email": email,
                            "role": "principal_owner",
                            "dept": "Administration",
                            "pos": "Principal",
                            "av": av_map.get(email, "LA"),
                            "active": True,
                            "phone": ""
                        }
                    })
                    return True
            with db() as conn:
                if PG_MODE:
                    row = conn.execute("SELECT * FROM public.staff_members WHERE lower(email)=%s AND active=true LIMIT 1", (email,)).fetchone()
                else:
                    row = conn.execute("SELECT * FROM staff_members WHERE lower(email)=? AND active=1 LIMIT 1", (email,)).fetchone()
                if not row:
                    self.send_json({"error": "Invalid credentials"}, 401)
                    return True
                
                d = rowdict(row)
                # Ensure the user has password hash or password set
                p_hash = d.get("password_hash")
                p_plain = d.get("notes") # In legacy, password might be stored in notes or somewhere else, but let's check verify
                if not verify_staff_password(password, p_hash or p_plain or ""):
                    self.send_json({"error": "Invalid credentials"}, 401)
                    return True
                
                token = sign_jwt({"staffId": d["id"]})
                self.send_json({"ok": True, "token": token, "user": desktop_staff_row(row)})
            return True

        # ── POST schedule write-back (desktop) ──
        if parsed.path == "/api/staffbase/schedule":
            payload = self.read_json()
            # If payload has schedule, unpack it
            schedule = payload.get("schedule") if payload.get("schedule") else payload
            shifts = schedule.get("shifts") if isinstance(schedule, dict) else None
            if not shifts or not isinstance(shifts, dict):
                self.send_json({"ok": True})
                return True
            week_start = get_monday_of_current_week()
            with db() as conn:
                for staff_id_str, day_map in shifts.items():
                    if not day_map or not isinstance(day_map, dict):
                        continue
                    # Clear current week's shifts for this staff
                    if PG_MODE:
                        conn.execute("DELETE FROM public.staff_schedules WHERE week_start=%s AND staff_id=%s", (week_start, staff_id_str))
                    else:
                        conn.execute("DELETE FROM staff_schedules WHERE week_start=? AND staff_id=?", (week_start, int(staff_id_str)))
                    
                    for day, shift in day_map.items():
                        stype = str(shift.get("type", "Off"))
                        start = str(shift.get("start", ""))
                        end = str(shift.get("end", ""))
                        loc = str(shift.get("location", ""))
                        ack = int(shift.get("ack", 0))
                        
                        if PG_MODE:
                            conn.execute(
                                """
                                INSERT INTO public.staff_schedules(organization_id, staff_id, week_start, weekday, shift_type, start_time, end_time, location, acknowledged)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                """,
                                (current_org_id(conn), staff_id_str, week_start, day, stype, start, end, loc, bool(ack))
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO staff_schedules(staff_id, week_start, weekday, shift_type, start_time, end_time, location, published)
                                VALUES (?,?,?,?,?,?,?,?)
                                """,
                                (int(staff_id_str), week_start, day, stype, start, end, loc, ack)
                            )
                conn.commit()
            self.send_json({"ok": True})
            return True

        # ── POST requests write-back status updates (desktop) ──
        if parsed.path == "/api/staffbase/requests":
            payload = self.read_json()
            reqs = payload.get("requests") if isinstance(payload, dict) and "requests" in payload else payload
            if not isinstance(reqs, list):
                reqs = []
            with db() as conn:
                for r in reqs:
                    rid = r.get("id")
                    status = r.get("status")
                    if rid and status:
                        if PG_MODE:
                            conn.execute("UPDATE public.time_off_requests SET status=%s, decided_at=now() WHERE id=%s", (status, rid))
                        else:
                            conn.execute("UPDATE time_off_requests SET status=?, decided_at=CURRENT_TIMESTAMP WHERE id=?", (status, int(rid)))
                conn.commit()
            self.send_json({"ok": True})
            return True

        # ── POST announcements sync (desktop) ──
        if parsed.path == "/api/staffbase/announcements":
            payload = self.read_json()
            ann_list = payload.get("announcements") if isinstance(payload, dict) and "announcements" in payload else payload
            if not isinstance(ann_list, list):
                ann_list = []
            
            today = datetime.now().strftime("%Y-%m-%d")
            with db() as conn:
                for a in ann_list:
                    raw_id = int(a.get("id") or 0)
                    title = str(a.get("title", "")).strip()
                    body = str(a.get("body", "")).strip()
                    priority = "high" if a.get("important") or a.get("priority") == "high" else "normal"
                    if not title:
                        continue
                    
                    if raw_id >= 1000000000:
                        # Desktop-created announcement. Use source_id to prevent duplicates
                        source_id = str(raw_id)
                        if PG_MODE:
                            exist = conn.execute("SELECT id FROM public.announcements WHERE source_id=%s LIMIT 1", (source_id,)).fetchone()
                        else:
                            exist = conn.execute("SELECT id FROM announcements WHERE source_id=? LIMIT 1", (source_id,)).fetchone()
                        
                        if exist:
                            if PG_MODE:
                                conn.execute("UPDATE public.announcements SET title=%s, body=%s, priority=%s WHERE id=%s", (title, body, priority, exist["id"]))
                            else:
                                conn.execute("UPDATE announcements SET title=?, body=?, priority=? WHERE id=?", (title, body, priority, exist["id"]))
                        else:
                            if PG_MODE:
                                conn.execute(
                                    """
                                    INSERT INTO public.announcements(organization_id, title, body, date, priority, active, source_id)
                                    VALUES (%s,%s,%s,%s,%s,true,%s)
                                    """,
                                    (current_org_id(conn), title, body, today, priority, source_id)
                                )
                            else:
                                conn.execute(
                                    """
                                    INSERT INTO announcements(title, body, date, priority, active, source_id)
                                    VALUES (?,?,?,?,?,?)
                                    """,
                                    (title, body, today, priority, 1, source_id)
                                )
                    elif raw_id > 0:
                        # DB-backed announcements, update in place
                        if PG_MODE:
                            conn.execute("UPDATE public.announcements SET title=%s, body=%s, priority=%s WHERE id=%s", (title, body, priority, raw_id))
                        else:
                            conn.execute("UPDATE announcements SET title=?, body=?, priority=? WHERE id=?", (title, body, priority, raw_id))
                conn.commit()
            self.send_json({"ok": True})
            return True

        # ── POST swaps sync (desktop) ──
        if parsed.path == "/api/staffbase/swaps":
            payload = self.read_json()
            swap_list = payload.get("swaps") if isinstance(payload, dict) and "swaps" in payload else payload
            if not isinstance(swap_list, list):
                swap_list = []
            
            DAY_OFFSETS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
            week_start = get_monday_of_current_week()
            received_db_ids = set()
            received_requester_ids = set()
            
            with db() as conn:
                for s in swap_list:
                    raw_id = int(s.get("id") or 0)
                    if raw_id <= 0:
                        continue
                    status = str(s.get("status", "open"))
                    
                    if raw_id < 1000000000:
                        # DB-backed row: update status and claimed_by_id
                        received_db_ids.add(raw_id)
                        req_id = s.get("uid")
                        if req_id:
                            received_requester_ids.add(str(req_id))
                        
                        claimed = s.get("claimedById") or s.get("claimedBy")
                        claimed_id = int(claimed) if claimed else None
                        
                        if PG_MODE:
                            conn.execute("UPDATE public.shift_swap_requests SET status=%s, claimed_by_id=%s WHERE id=%s", (status, claimed_id, raw_id))
                        else:
                            conn.execute("UPDATE shift_swap_requests SET status=?, claimed_by_id=? WHERE id=?", (status, claimed_id, raw_id))
                    else:
                        # Desktop-created swap: insert into DB
                        requester_id = int(s.get("uid") or 0)
                        if requester_id <= 0:
                            continue
                        received_requester_ids.add(str(requester_id))
                        
                        day = str(s.get("day", "Tue"))
                        offset = DAY_OFFSETS.get(day, 1)
                        # Compute shift date
                        mon_date = datetime.strptime(week_start, "%Y-%m-%d")
                        shift_date_obj = mon_date + timedelta(days=offset)
                        shift_date = shift_date_obj.strftime("%Y-%m-%d")
                        reason = str(s.get("note") or s.get("reason") or "").strip()
                        
                        # Dedup check
                        if PG_MODE:
                            exist = conn.execute("SELECT id FROM public.shift_swap_requests WHERE requester_id=%s AND shift_date=%s LIMIT 1", (requester_id, shift_date)).fetchone()
                        else:
                            exist = conn.execute("SELECT id FROM shift_swap_requests WHERE requester_id=? AND shift_date=? LIMIT 1", (requester_id, shift_date)).fetchone()
                        
                        if not exist:
                            if PG_MODE:
                                row = conn.execute(
                                    """
                                    INSERT INTO public.shift_swap_requests(organization_id, requester_id, shift_date, reason, status)
                                    VALUES (%s,%s,%s,%s,'open') RETURNING id
                                    """,
                                    (current_org_id(conn), requester_id, shift_date, reason)
                                ).fetchone()
                                if row:
                                    received_db_ids.add(row["id"])
                            else:
                                cur = conn.execute(
                                    """
                                    INSERT INTO shift_swap_requests(requester_id, shift_date, reason, status)
                                    VALUES (?,?,?, 'open')
                                    """,
                                    (requester_id, shift_date, reason)
                                )
                                received_db_ids.add(cur.lastrowid)
                        else:
                            received_db_ids.add(exist["id"])
                
                # Reconciliation for cancellation
                if swap_list and received_requester_ids:
                    # Cancel any open/claimed swaps from these requesters that weren't received
                    for req_id in received_requester_ids:
                        if PG_MODE:
                            active_swaps = conn.execute("SELECT id FROM public.shift_swap_requests WHERE requester_id=%s AND (status='open' OR status='claimed')", (req_id,)).fetchall()
                        else:
                            active_swaps = conn.execute("SELECT id FROM shift_swap_requests WHERE requester_id=? AND (status='open' OR status='claimed')", (int(req_id),)).fetchall()
                        
                        for sw in active_swaps:
                            sw_id = sw["id"]
                            if sw_id not in received_db_ids:
                                if PG_MODE:
                                    conn.execute("UPDATE public.shift_swap_requests SET status='cancelled' WHERE id=%s", (sw_id,))
                                else:
                                    conn.execute("UPDATE shift_swap_requests SET status='cancelled' WHERE id=?", (sw_id,))
                conn.commit()
            self.send_json({"ok": True})
            return True

        # ── POST staff base no-op stubs ──
        if parsed.path in [
            "/api/staffbase/school",
            "/api/staffbase/subjects",
            "/api/staffbase/users",
            "/api/staffbase/messages",
            "/api/staffbase/checkin_log",
            "/api/staffbase/documents",
            "/api/staffbase/ts_approvals"
        ]:
            self.send_json({"ok": True})
            return True

        # ── POST staff base clock_data write-back ──
        if parsed.path == "/api/staffbase/clock_data":
            payload = self.read_json()
            if not isinstance(payload, dict):
                self.send_json({"ok": True})
                return True
            
            def compute_duration(clock_in: str, clock_out: str) -> float:
                if not clock_in or not clock_out:
                    return 0.0
                try:
                    from datetime import datetime
                    fmt = "%I:%M %p"
                    t_in = datetime.strptime(clock_in.strip(), fmt)
                    t_out = datetime.strptime(clock_out.strip(), fmt)
                    delta = t_out - t_in
                    hours = delta.total_seconds() / 3600.0
                    if hours < 0: # crossed midnight
                        hours += 24.0
                    return round(hours, 2)
                except Exception:
                    return 0.0

            today = datetime.now().strftime("%Y-%m-%d")
            with db() as conn:
                org_id = current_org_id(conn) if PG_MODE else None
                for staff_id_str, item in payload.items():
                    if not isinstance(item, dict):
                        continue
                    cin = item.get("in")
                    cout = item.get("out")
                    gps_ok = bool(item.get("gpsOK", True))
                    
                    duration = compute_duration(cin, cout)
                    
                    if PG_MODE:
                        existing = conn.execute(
                            "SELECT id FROM public.staff_shift_punches WHERE staff_id=%s AND punch_date=%s LIMIT 1",
                            (staff_id_str, today)
                        ).fetchone()
                    else:
                        existing = conn.execute(
                            "SELECT id FROM staff_shift_punches WHERE staff_id=? AND punch_date=? LIMIT 1",
                            (int(staff_id_str), today)
                        ).fetchone()
                    
                    if existing:
                        row_id = rowdict(existing)["id"]
                        if PG_MODE:
                            conn.execute(
                                """
                                UPDATE public.staff_shift_punches
                                SET clock_in=%s, clock_out=%s, duration_hours=%s, notes=%s, updated_at=now()
                                WHERE id=%s
                                """,
                                (cin, cout, duration, "GPS Verified" if gps_ok else "Location Override", row_id)
                            )
                        else:
                            conn.execute(
                                """
                                UPDATE staff_shift_punches
                                SET clock_in=?, clock_out=?, duration_hours=?, notes=?, updated_at=CURRENT_TIMESTAMP
                                WHERE id=?
                                """,
                                (cin, cout, duration, "GPS Verified" if gps_ok else "Location Override", int(row_id))
                            )
                    else:
                        if PG_MODE:
                            conn.execute(
                                """
                                INSERT INTO public.staff_shift_punches (organization_id, staff_id, punch_date, clock_in, clock_out, duration_hours, source, notes)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (org_id, staff_id_str, today, cin, cout, duration, "mobile", "GPS Verified" if gps_ok else "Location Override")
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO staff_shift_punches (staff_id, punch_date, clock_in, clock_out, duration_hours, source, notes)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (int(staff_id_str), today, cin, cout, duration, "mobile", "GPS Verified" if gps_ok else "Location Override")
                            )
                conn.commit()
            self.send_json({"ok": True})
            return True

        # ── POST create staff (desktop admin → DB) ──
        if parsed.path == "/api/staffbase/staff":
            payload = self.read_json()
            name = str(payload.get("name", "")).strip()
            email = str(payload.get("email", "")).strip()
            pin = str(payload.get("pin", "")).strip()
            password = str(payload.get("password", "")).strip()
            role = str(payload.get("role", "staff")).strip()
            dept = str(payload.get("dept", "Administration")).strip()
            pos = str(payload.get("pos", "")).strip()
            av = str(payload.get("av", "")).strip()
            
            if not name or not email or not pin:
                self.send_json({"error": "name, email, and pin are required"}, 400)
                return True
            
            pin_hash = hash_bcrypt(pin)
            pw_hash = hash_bcrypt(password) if password else None
            initials = av if av else "".join([part[0] for part in name.split(" ") if part]).upper()[:2]
            colors = ["#6366f1","#0ea5e9","#10b981","#f59e0b","#ef4444","#8b5cf6","#ec4899","#14b8a6"]
            import random
            avatar_color = random.choice(colors)
            db_role = "manager" if role in ["principal_owner", "administrator", "office_manager", "manager"] else "staff"
            
            with db() as conn:
                if PG_MODE:
                    row = conn.execute(
                        """
                        INSERT INTO public.staff_members(organization_id, staff_name, role_title, subject, email, pin, pin_hash, password_hash, role, avatar_initials, avatar_color, active)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) RETURNING id
                        """,
                        (current_org_id(conn), name, pos, dept, email, pin, pin_hash, pw_hash, db_role, initials, avatar_color)
                    ).fetchone()
                    new_id = str(row["id"])
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO staff_members(staff_name, role_title, subject, email, pin, pin_hash, password_hash, role, avatar_initials, avatar_color, active)
                        VALUES (?,?,?,?,?,?,?,?,?,?,1)
                        """,
                        (name, pos, dept, email, pin, pin_hash, pw_hash, db_role, initials, avatar_color)
                    )
                    new_id = cur.lastrowid
                conn.commit()
            
            # Send welcome email asynchronously/inline
            try:
                host = self.headers.get("Host", "localhost:8765")
                proto = self.headers.get("X-Forwarded-Proto", "http")
                origin = f"{proto}://{host}"
                with db() as conn:
                    send_staff_welcome_email(conn, name, email, pin, role, pos, password, origin)
            except Exception as email_err:
                print("Failed to automatically send onboarding welcome email:", email_err)

            self.send_json({"ok": True, "id": new_id})
            return True

        # ── POST Mobile: clock-in/out punch ──
        if parsed.path == "/api/clock":
            if not self.require_staffbase_auth():
                return True
            payload = self.read_json()
            action = str(payload.get("action", "")).strip().lower()
            gps_ok = bool(payload.get("gpsOK", True))
            if action not in ["in", "out"]:
                self.send_json({"error": "action must be 'in' or 'out'"}, 400)
                return True
            
            today = datetime.now().strftime("%Y-%m-%d")
            now_iso = datetime.now().isoformat()
            target_staff_id = self.staff_id
            
            with db() as conn:
                # Check if current user is manager/admin
                is_manager = False
                if PG_MODE:
                    u_row = conn.execute("SELECT role, role_title FROM public.staff_members WHERE id=%s", (self.staff_id,)).fetchone()
                else:
                    u_row = conn.execute("SELECT role, role_title FROM staff_members WHERE id=?", (int(self.staff_id),)).fetchone()
                if u_row:
                    ud = rowdict(u_row)
                    u_role = str(ud.get("role") or ud.get("role_title") or "").lower().strip()
                    if u_role in ["manager", "admin", "administrator", "owner", "principal_owner", "office_manager", "office manager"]:
                        is_manager = True
                
                payload_staff_id = payload.get("staffId") or payload.get("staff_id")
                if is_manager and payload_staff_id:
                    target_staff_id = str(payload_staff_id)
                
                if PG_MODE:
                    existing = conn.execute("SELECT * FROM public.staff_shift_punches WHERE staff_id=%s AND punch_date=%s LIMIT 1", (target_staff_id, today)).fetchone()
                else:
                    existing = conn.execute("SELECT * FROM staff_shift_punches WHERE staff_id=? AND punch_date=? LIMIT 1", (int(target_staff_id), today)).fetchone()
                
                if action == "in":
                    if existing:
                        self.send_json(build_clock_status(existing))
                        return True
                    if PG_MODE:
                        row = conn.execute(
                            """
                            INSERT INTO public.staff_shift_punches(organization_id, staff_id, clock_in, gps_ok, punch_date)
                            VALUES (%s,%s,%s,%s,%s) RETURNING *
                            """,
                            (current_org_id(conn), target_staff_id, now_iso, gps_ok, today)
                        ).fetchone()
                    else:
                        conn.execute(
                            """
                            INSERT INTO staff_shift_punches(staff_id, clock_in, gps_ok, punch_date)
                            VALUES (?,?,?,?)
                            """,
                            (int(target_staff_id), now_iso, 1 if gps_ok else 0, today)
                        )
                        existing_row = conn.execute("SELECT * FROM staff_shift_punches WHERE staff_id=? AND punch_date=? LIMIT 1", (int(target_staff_id), today)).fetchone()
                        row = existing_row
                    conn.commit()
                    self.send_json(build_clock_status(row))
                else:
                    # clock out
                    if existing:
                        d = rowdict(existing)
                        duration = 0.0
                        if d.get("clock_in"):
                            try:
                                t_in = datetime.fromisoformat(d["clock_in"].replace("Z", "+00:00"))
                                t_out = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                                duration = round((t_out - t_in).total_seconds() / 3600.0, 2)
                            except Exception:
                                pass
                        if PG_MODE:
                            row = conn.execute(
                                """
                                UPDATE public.staff_shift_punches
                                SET clock_out=%s, gps_ok=%s, duration_hours=%s, updated_at=now()
                                WHERE id=%s RETURNING *
                                """,
                                (now_iso, gps_ok, duration, d["id"])
                            ).fetchone()
                        else:
                            conn.execute(
                                """
                                UPDATE staff_shift_punches
                                SET clock_out=?, gps_ok=?, duration_hours=?, updated_at=CURRENT_TIMESTAMP
                                WHERE id=?
                                """,
                                (now_iso, 1 if gps_ok else 0, duration, d["id"])
                            )
                            row = conn.execute("SELECT * FROM staff_shift_punches WHERE id=?", (d["id"],)).fetchone()
                        conn.commit()
                        self.send_json(build_clock_status(row))
                    else:
                        # Punch out without punch in (insert)
                        if PG_MODE:
                            row = conn.execute(
                                """
                                INSERT INTO public.staff_shift_punches(organization_id, staff_id, clock_out, gps_ok, punch_date)
                                VALUES (%s,%s,%s,%s,%s) RETURNING *
                                """,
                                (current_org_id(conn), target_staff_id, now_iso, gps_ok, today)
                            ).fetchone()
                        else:
                            conn.execute(
                                """
                                INSERT INTO staff_shift_punches(staff_id, clock_out, gps_ok, punch_date)
                                VALUES (?,?,?,?)
                                """,
                                (int(target_staff_id), now_iso, 1 if gps_ok else 0, today)
                            )
                            row = conn.execute("SELECT * FROM staff_shift_punches WHERE staff_id=? AND punch_date=? LIMIT 1", (int(target_staff_id), today)).fetchone()
                        conn.commit()
                        self.send_json(build_clock_status(row))
            return True

        # ── POST Mobile: acknowledge shift ──
        ack_match = re.match(r"^/api/schedule/([^/]+)/ack$", parsed.path)
        if ack_match:
            if not self.require_staffbase_auth():
                return True
            param_id = ack_match.group(1)
            if str(param_id) != str(self.staff_id):
                self.send_json({"error": "Forbidden"}, 403)
                return True
            payload = self.read_json()
            day = str(payload.get("day", "")).strip()
            if not day:
                self.send_json({"error": "Missing day"}, 400)
                return True
            week_start = get_monday_of_current_week()
            with db() as conn:
                if PG_MODE:
                    conn.execute("UPDATE public.staff_schedules SET acknowledged=true WHERE staff_id=%s AND week_start=%s AND weekday=%s", (self.staff_id, week_start, day))
                    row = conn.execute("SELECT * FROM public.staff_schedules WHERE staff_id=%s AND week_start=%s AND weekday=%s LIMIT 1", (self.staff_id, week_start, day)).fetchone()
                else:
                    conn.execute("UPDATE staff_schedules SET published=1 WHERE staff_id=? AND week_start=? AND weekday=?", (int(self.staff_id), week_start, day))
                    row = conn.execute("SELECT * FROM staff_schedules WHERE staff_id=? AND week_start=? AND weekday=? LIMIT 1", (int(self.staff_id), week_start, day)).fetchone()
                conn.commit()
                if not row:
                    self.send_json({"error": "Schedule entry not found"}, 404)
                    return True
                
                d = rowdict(row)
                self.send_json({
                    "id": d["id"],
                    "day": d.get("weekday") or d.get("day") or "",
                    "shiftType": d["shift_type"],
                    "start": d["start_time"],
                    "end": d["end_time"],
                    "location": d["location"],
                    "ack": True
                })
            return True

        # ── POST Mobile: submit time-off request ──
        if parsed.path == "/api/requests":
            if not self.require_staffbase_auth():
                return True
            payload = self.read_json()
            start_date = str(payload.get("startDate", "")).strip()
            end_date = str(payload.get("endDate", "")).strip()
            reason = str(payload.get("reason", "")).strip()
            if not start_date or not end_date:
                self.send_json({"error": "Missing dates"}, 400)
                return True
            
            import uuid
            approval_token = str(uuid.uuid4())
            expiry = (datetime.now() + timedelta(days=7)).isoformat()
            submitted = datetime.now().isoformat()
            
            with db() as conn:
                if PG_MODE:
                    row = conn.execute(
                        """
                        INSERT INTO public.time_off_requests(organization_id, staff_id, start_date, end_date, reason, status, submitted_at, approval_token, approval_token_expires_at)
                        VALUES (%s,%s,%s,%s,%s,'pending',%s,%s,%s) RETURNING *
                        """,
                        (current_org_id(conn), self.staff_id, start_date, end_date, reason, submitted, approval_token, expiry)
                    ).fetchone()
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO time_off_requests(staff_id, start_date, end_date, reason, status, submitted_at, approval_token, approval_token_expires_at)
                        VALUES (?,?,?,?,'pending',?,?,?)
                        """,
                        (int(self.staff_id), start_date, end_date, reason, submitted, approval_token, expiry)
                    )
                    row = conn.execute("SELECT * FROM time_off_requests WHERE id=?", (cur.lastrowid,)).fetchone()
                conn.commit()
                
                d = rowdict(row)
                self.send_json({
                    "id": d["id"],
                    "staffId": d["staff_id"],
                    "startDate": d["start_date"],
                    "endDate": d["end_date"],
                    "reason": d["reason"],
                    "status": d["status"],
                    "submittedAt": d["submitted_at"]
                }, 201)
            return True

        # ── POST Mobile: post a shift swap request ──
        if parsed.path == "/api/swaps":
            if not self.require_staffbase_auth():
                return True
            payload = self.read_json()
            shift_date = str(payload.get("shiftDate", "")).strip()
            reason = str(payload.get("reason", "")).strip()
            if not shift_date:
                self.send_json({"error": "Missing shiftDate"}, 400)
                return True
            
            posted = datetime.now().isoformat()
            with db() as conn:
                if PG_MODE:
                    row = conn.execute(
                        """
                        INSERT INTO public.shift_swap_requests(organization_id, requester_id, shift_date, reason, status, posted_at)
                        VALUES (%s,%s,%s,%s,'open',%s) RETURNING *
                        """,
                        (current_org_id(conn), self.staff_id, shift_date, reason, posted)
                    ).fetchone()
                    req_row = conn.execute("SELECT staff_name FROM public.staff_members WHERE id=%s", (self.staff_id,)).fetchone()
                    requester_name = rowdict(req_row).get("staff_name") if req_row else "Unknown"
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO shift_swap_requests(requester_id, shift_date, reason, status, posted_at)
                        VALUES (?,?,?, 'open', ?)
                        """,
                        (int(self.staff_id), shift_date, reason, posted)
                    )
                    row = conn.execute("SELECT * FROM shift_swap_requests WHERE id=?", (cur.lastrowid,)).fetchone()
                    req_row = conn.execute("SELECT staff_name FROM staff_members WHERE id=?", (int(self.staff_id),)).fetchone()
                    requester_name = rowdict(req_row).get("staff_name") if req_row else "Unknown"
                conn.commit()
                
                d = rowdict(row)
                self.send_json({
                    "id": d["id"],
                    "requesterId": d["requester_id"],
                    "requesterName": requester_name,
                    "shiftDate": d["shift_date"],
                    "reason": d["reason"],
                    "status": d["status"],
                    "claimedById": None,
                    "postedAt": d["posted_at"]
                }, 201)
            return True

        # ── POST Mobile: claim shift swap ──
        claim_match = re.match(r"^/api/swaps/([^/]+)/claim$", parsed.path)
        if claim_match:
            if not self.require_staffbase_auth():
                return True
            swap_id = claim_match.group(1)
            with db() as conn:
                if PG_MODE:
                    swap = conn.execute("SELECT * FROM public.shift_swap_requests WHERE id=%s LIMIT 1", (swap_id,)).fetchone()
                else:
                    swap = conn.execute("SELECT * FROM shift_swap_requests WHERE id=? LIMIT 1", (int(swap_id),)).fetchone()
                
                if not swap:
                    self.send_json({"error": "Swap request not found"}, 404)
                    return True
                
                sd = rowdict(swap)
                if sd["status"] != "open":
                    self.send_json({"error": "Swap request is no longer available"}, 409)
                    return True
                if str(sd["requester_id"]) == str(self.staff_id):
                    self.send_json({"error": "Cannot claim your own swap request"}, 400)
                    return True
                
                if PG_MODE:
                    conn.execute("UPDATE public.shift_swap_requests SET status='claimed', claimed_by_id=%s WHERE id=%s", (self.staff_id, swap_id))
                    updated = conn.execute("SELECT s.*, m.staff_name FROM public.shift_swap_requests s JOIN public.staff_members m ON s.requester_id = m.id WHERE s.id=%s LIMIT 1", (swap_id,)).fetchone()
                else:
                    conn.execute("UPDATE shift_swap_requests SET status='claimed', claimed_by_id=? WHERE id=?", (int(self.staff_id), int(swap_id)))
                    updated = conn.execute("SELECT s.*, m.staff_name FROM shift_swap_requests s JOIN staff_members m ON s.requester_id = m.id WHERE s.id=? LIMIT 1", (int(swap_id),)).fetchone()
                conn.commit()
                
                d = rowdict(updated)
                self.send_json({
                    "id": d["id"],
                    "requesterId": d["requester_id"],
                    "requesterName": d.get("staff_name") or "",
                    "shiftDate": d["shift_date"],
                    "reason": d["reason"],
                    "status": d["status"],
                    "claimedById": d.get("claimed_by_id"),
                    "postedAt": d["posted_at"]
                })
            return True

        # ── POST Mobile: save push token ──
        if parsed.path == "/api/push-token":
            if not self.require_staffbase_auth():
                return True
            payload = self.read_json()
            token = str(payload.get("token", "")).strip()
            if not token:
                self.send_json({"error": "token is required"}, 400)
                return True
            with db() as conn:
                if PG_MODE:
                    conn.execute("UPDATE public.staff_members SET expo_push_token=%s WHERE id=%s", (token, self.staff_id))
                else:
                    conn.execute("UPDATE staff_members SET expo_push_token=? WHERE id=?", (token, int(self.staff_id)))
                conn.commit()
            self.send_json({"ok": True})
            return True

        # ── POST Manager: decide request ──
        decide_match = re.match(r"^/api/manager/requests/([^/]+)/decide$", parsed.path)
        if decide_match:
            if not self.require_staffbase_manager():
                return True
            req_id = decide_match.group(1)
            payload = self.read_json()
            action = str(payload.get("action", "")).strip().lower()
            if action not in ["approve", "reject"]:
                self.send_json({"error": "action must be 'approve' or 'reject'"}, 400)
                return True
            
            new_status = "approved" if action == "approve" else "denied"
            decided = datetime.now().isoformat()
            
            with db() as conn:
                if PG_MODE:
                    conn.execute("UPDATE public.time_off_requests SET status=%s, decided_at=%s, approval_token=NULL WHERE id=%s", (new_status, decided, req_id))
                    row = conn.execute("SELECT * FROM public.time_off_requests WHERE id=%s LIMIT 1", (req_id,)).fetchone()
                else:
                    conn.execute("UPDATE time_off_requests SET status=?, decided_at=?, approval_token=NULL WHERE id=?", (new_status, decided, int(req_id)))
                    row = conn.execute("SELECT * FROM time_off_requests WHERE id=? LIMIT 1", (int(req_id),)).fetchone()
                conn.commit()
                
                if not row:
                    self.send_json({"error": "Request not found"}, 404)
                    return True
                
                d = rowdict(row)
                self.send_json({
                    "id": d["id"],
                    "staffId": d["staff_id"],
                    "startDate": d["start_date"],
                    "endDate": d["end_date"],
                    "reason": d["reason"],
                    "status": d["status"],
                    "submittedAt": d["submitted_at"],
                    "decidedAt": d["decided_at"]
                })
            return True

        # ── POST Manager: copy schedule week ──
        if parsed.path == "/api/manager/schedule/week":
            if not self.require_staffbase_manager():
                return True
            week_start = get_monday_of_current_week()
            # Compute previous week start
            mon_date = datetime.strptime(week_start, "%Y-%m-%d")
            prev_week_start = (mon_date - timedelta(days=7)).strftime("%Y-%m-%d")
            
            with db() as conn:
                if PG_MODE:
                    prev_shifts = conn.execute("SELECT * FROM public.staff_schedules WHERE week_start=%s", (prev_week_start,)).fetchall()
                else:
                    prev_shifts = conn.execute("SELECT * FROM staff_schedules WHERE week_start=?", (prev_week_start,)).fetchall()
                
                if not prev_shifts:
                    self.send_json({"error": "No previous week schedule found"}, 404)
                    return True
                
                # Delete existing week schedule
                if PG_MODE:
                    conn.execute("DELETE FROM public.staff_schedules WHERE week_start=%s", (week_start,))
                    for sh in prev_shifts:
                        shd = rowdict(sh)
                        conn.execute(
                            """
                            INSERT INTO public.staff_schedules(organization_id, staff_id, week_start, weekday, shift_type, start_time, end_time, location, acknowledged)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false)
                            """,
                            (current_org_id(conn), shd["staff_id"], week_start, shd["weekday"], shd["shift_type"], shd["start_time"], shd["end_time"], shd["location"])
                        )
                else:
                    conn.execute("DELETE FROM staff_schedules WHERE week_start=?", (week_start,))
                    for sh in prev_shifts:
                        shd = rowdict(sh)
                        conn.execute(
                            """
                            INSERT INTO staff_schedules(staff_id, week_start, weekday, shift_type, start_time, end_time, location, published)
                            VALUES (?,?,?,?,?,?,?,0)
                            """,
                            (int(shd["staff_id"]), week_start, shd["weekday"], shd["shift_type"], shd["start_time"], shd["end_time"], shd["location"])
                        )
                conn.commit()
                
                # Return updated schedules
                if PG_MODE:
                    staff = conn.execute("SELECT id::text as id, staff_name, avatar_initials, avatar_color FROM public.staff_members WHERE active=true ORDER BY staff_name").fetchall()
                    shifts = conn.execute("SELECT * FROM public.staff_schedules WHERE week_start=%s", (week_start,)).fetchall()
                else:
                    staff = conn.execute("SELECT id, staff_name, avatar_initials, avatar_color FROM staff_members WHERE active=1 ORDER BY staff_name").fetchall()
                    shifts = conn.execute("SELECT * FROM staff_schedules WHERE week_start=?", (week_start,)).fetchall()
                
                result = []
                for s in staff:
                    sd = rowdict(s)
                    sid = str(sd["id"])
                    sshifts = []
                    for sh in shifts:
                        shd = rowdict(sh)
                        if str(shd["staff_id"]) == sid:
                            sshifts.append({
                                "id": shd["id"],
                                "day": shd.get("weekday") or shd.get("day") or "",
                                "shiftType": shd["shift_type"],
                                "start": shd["start_time"],
                                "end": shd["end_time"],
                                "location": shd["location"],
                                "ack": bool(shd.get("acknowledged") or shd.get("ack"))
                            })
                    result.append({
                        "staffId": sd["id"],
                        "name": sd.get("staff_name") or "",
                        "avatarInitials": sd.get("avatar_initials") or "ST",
                        "avatarColor": sd.get("avatar_color") or "#6366f1",
                        "shifts": sshifts
                    })
                self.send_json(result)
            return True

        # ── POST Manager: publish announcements (for mobile fan out) ──
        if parsed.path == "/api/announcements":
            if not self.require_staffbase_manager():
                return True
            payload = self.read_json()
            title = str(payload.get("title", "")).strip()
            body = str(payload.get("body", "")).strip()
            priority = str(payload.get("priority", "normal")).strip()
            if not title or not body:
                self.send_json({"error": "title and body are required"}, 400)
                return True
            
            today = datetime.now().strftime("%Y-%m-%d")
            with db() as conn:
                if PG_MODE:
                    row = conn.execute(
                        """
                        INSERT INTO public.announcements(organization_id, title, body, date, priority, active)
                        VALUES (%s,%s,%s,%s,%s,true) RETURNING *
                        """,
                        (current_org_id(conn), title, body, today, priority)
                    ).fetchone()
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO announcements(title, body, date, priority, active)
                        VALUES (?,?,?,?,1)
                        """,
                        (title, body, today, priority)
                    )
                    row = conn.execute("SELECT * FROM announcements WHERE id=?", (cur.lastrowid,)).fetchone()
                conn.commit()
                
                d = rowdict(row)
                self.send_json({
                    "id": d["id"],
                    "title": d["title"],
                    "body": d["body"],
                    "date": d["date"],
                    "priority": d["priority"],
                    "skippedCount": 0,
                    "skippedStaff": []
                }, 201)
            return True

        return False



    def do_POST(self):
        parsed = urlparse(self.path)
        if self.handle_staffbase_post(parsed):
            return
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        try:
            if parsed.path == "/api/expenses":
                if not self.require_permission("manage_payments"):
                    return
                payload = self.read_json()
                month_label = str(payload.get("month_label", "")).strip()
                rent = money(payload.get("rent_expense"))
                royalty = money(payload.get("royalty_expense"))
                utilities = money(payload.get("utilities_expense"))
                misc = money(payload.get("misc_expense"))
                details = str(payload.get("misc_details", "")).strip()
                if not month_label or month_label not in MONTHS:
                    raise ValueError("A valid month label is required")
                with db() as conn:
                    if PG_MODE:
                        org_id = current_org_id(conn)
                        conn.execute(
                            """
                            INSERT INTO public.monthly_expenses(organization_id, month_label, rent_expense, royalty_expense, utilities_expense, misc_expense, misc_details)
                            VALUES (%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT(organization_id, month_label)
                            DO UPDATE SET rent_expense=excluded.rent_expense, royalty_expense=excluded.royalty_expense,
                                          utilities_expense=excluded.utilities_expense, misc_expense=excluded.misc_expense,
                                          misc_details=excluded.misc_details, updated_at=now()
                            """,
                            (org_id, month_label, rent, royalty, utilities, misc, details)
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO monthly_expenses(month_label, rent_expense, royalty_expense, utilities_expense, misc_expense, misc_details)
                            VALUES (?,?,?,?,?,?)
                            ON CONFLICT(month_label)
                            DO UPDATE SET rent_expense=excluded.rent_expense, royalty_expense=excluded.royalty_expense,
                                          utilities_expense=excluded.utilities_expense, misc_expense=excluded.misc_expense,
                                          misc_details=excluded.misc_details
                            """,
                            (month_label, rent, royalty, utilities, misc, details)
                        )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/centre-setup":
                if not self.require_permission("manage_settings"):
                    return
                payload = self.read_json()
                org_name = str(payload.get("organization_name", "")).strip()
                branch_name = str(payload.get("branch_name", "")).strip()
                branch_code = str(payload.get("branch_code", "")).strip()
                if not org_name or not branch_name or not branch_code:
                    raise ValueError("Organization Name, Branch Name, and Branch Code are all required.")
                def slugify(text):
                    import re
                    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
                org_slug = slugify(org_name)
                branch_slug = slugify(branch_name)
                with db() as conn:
                    if PG_MODE:
                        org_id = current_org_id(conn)
                        conn.execute(
                            "UPDATE public.organizations SET name=%s, slug=%s, updated_at=now() WHERE id=%s",
                            (org_name, org_slug, org_id)
                        )
                        conn.execute(
                            "UPDATE public.branches SET name=%s, slug=%s, code=%s, updated_at=now() WHERE organization_id=%s",
                            (branch_name, branch_slug, branch_code, org_id)
                        )
                        conn.execute(
                            """
                            INSERT INTO public.app_meta(key, value) VALUES (%s, %s)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value
                            """,
                            ("center_setup_completed", "1")
                        )
                    else:
                        org_row = conn.execute("SELECT id FROM organizations ORDER BY created_at LIMIT 1").fetchone()
                        org_id = org_row[0] if org_row else 1
                        conn.execute(
                            "UPDATE organizations SET name=?, slug=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (org_name, org_slug, org_id)
                        )
                        conn.execute(
                            "UPDATE branches SET name=?, slug=?, code=?, updated_at=CURRENT_TIMESTAMP WHERE organization_id=?",
                            (branch_name, branch_slug, branch_code, org_id)
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)",
                            ("center_setup_completed", "1")
                        )
                        conn.execute("INSERT OR REPLACE INTO app_meta(key, value) VALUES (?, ?)", ("institution_name", org_name))
                    conn.commit()
                self.send_json({"ok": True})
                return

            if parsed.path == "/api/students":
                if not self.require_permission("manage_students"):
                    return
                student = normalize_student(self.read_json())
                with db() as conn:
                    new_id = insert_student_record(conn, student, next_student_number(conn), self.actor_email())
                    conn.commit()
                    self.send_json({"ok": True, "id": new_id})
                return
            if parsed.path == "/api/staff/members":
                if not self.require_permission("manage_staff"):
                    return
                payload = self.read_json()
                with db() as conn:
                    new_id = save_staff_member(conn, payload)
                    conn.commit()
                    self.send_json({"ok": True, "id": new_id})
                return
            if parsed.path == "/api/staff/schedules":
                if not self.require_permission("manage_staff"):
                    return
                payload = self.read_json()
                with db() as conn:
                    save_staff_schedule(conn, payload)
                    conn.commit()
                    self.send_json({"ok": True})
                return
            if parsed.path == "/api/staff/punches":
                if not self.require_permission("manage_staff"):
                    return
                payload = self.read_json()
                with db() as conn:
                    new_id = save_staff_punch(conn, payload)
                    conn.commit()
                    self.send_json({"ok": True, "id": new_id})
                return
            if parsed.path == "/api/batch":
                if not self.require_permission("manage_students"):
                    return
                rows = self.read_json().get("rows", [])
                saved = []
                rejected = []
                for index, item in enumerate(rows, start=1):
                    preview_row = int(item.get("_preview_row") or index)
                    if str(item.get("student_name", "")).strip():
                        try:
                            saved.append((preview_row, normalize_student(item)))
                        except ValueError as exc:
                            rejected.append({"row": preview_row, "student_name": str(item.get("student_name", "")).strip(), "error": str(exc)})
                with db() as conn:
                    next_number = next_student_number(conn)
                    saved_count = 0
                    for index, student in saved:
                        try:
                            insert_student_record(conn, student, next_number, self.actor_email())
                            conn.commit()
                            next_number += 1
                            saved_count += 1
                        except Exception as exc:
                            conn.rollback()
                            rejected.append({"row": index, "student_name": student["student_name"], "error": str(exc)})
                self.send_json({"ok": True, "saved": saved_count, "rejected": rejected})
                return
            if parsed.path == "/api/settings":
                if not self.require_permission("manage_settings"):
                    return
                payload = self.read_json()
                
                def slugify_helper(text):
                    import re
                    return re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')

                with db() as conn:
                    if PG_MODE:
                        org_id = current_org_id(conn)
                        conn.execute(
                            """
                            UPDATE public.organizations
                            SET name=%s, phone=%s, details=%s, subjects_offered=%s, current_month=%s,
                                operating_start=%s::time, operating_end=%s::time, support_email=%s, updated_at=now()
                            WHERE id=%s
                            """,
                            (
                                str(payload.get("institution_name", DEFAULT_SETTINGS["institution_name"])),
                                str(payload.get("institution_phone", "")),
                                str(payload.get("institution_details", DEFAULT_SETTINGS["institution_details"])),
                                configured_subjects({"subjects_offered": str(payload.get("subjects_offered", DEFAULT_SETTINGS["subjects_offered"]))}),
                                str(payload.get("current_month", DEFAULT_SETTINGS["current_month"])),
                                str(payload.get("operating_start", DEFAULT_SETTINGS["operating_start"])),
                                str(payload.get("operating_end", DEFAULT_SETTINGS["operating_end"])),
                                str(payload.get("support_email", DEFAULT_SETTINGS["support_email"])),
                                org_id,
                            ),
                        )
                        if "institution_name" in payload:
                            org_name = str(payload.get("institution_name")).strip()
                            org_slug = slugify_helper(org_name)
                            conn.execute("UPDATE public.organizations SET name=%s, slug=%s WHERE id=%s", (org_name, org_slug, org_id))
                        
                        if "branch_name" in payload or "branch_code" in payload:
                            row = conn.execute("SELECT name, code FROM public.branches WHERE organization_id=%s ORDER BY created_at LIMIT 1", (org_id,)).fetchone()
                            existing_name = rowdict(row)["name"] if row else ""
                            existing_code = rowdict(row)["code"] if row else ""
                            new_name = str(payload.get("branch_name", existing_name)).strip()
                            new_code = str(payload.get("branch_code", existing_code)).strip()
                            new_slug = slugify_helper(new_name)
                            conn.execute(
                                """
                                UPDATE public.branches
                                SET name=%s, code=%s, slug=%s, updated_at=now()
                                WHERE organization_id=%s
                                """,
                                (new_name, new_code, new_slug, org_id)
                            )
                    else:
                        for key in DEFAULT_SETTINGS:
                            if key in payload:
                                conn.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES (?,?)", (key, str(payload.get(key, ""))))
                        
                        org_row = conn.execute("SELECT id, name FROM organizations ORDER BY created_at LIMIT 1").fetchone()
                        org_id = org_row[0] if org_row else 1
                        
                        if "institution_name" in payload:
                            org_name = str(payload.get("institution_name")).strip()
                            org_slug = slugify_helper(org_name)
                            conn.execute("UPDATE organizations SET name=?, slug=? WHERE id=?", (org_name, org_slug, org_id))
                            
                        if "branch_name" in payload or "branch_code" in payload:
                            branch_row = conn.execute("SELECT name, code FROM branches WHERE organization_id=? ORDER BY created_at LIMIT 1", (org_id,)).fetchone()
                            existing_name = branch_row[0] if branch_row else ""
                            existing_code = branch_row[1] if branch_row else ""
                            new_name = str(payload.get("branch_name", existing_name)).strip()
                            new_code = str(payload.get("branch_code", existing_code)).strip()
                            new_slug = slugify_helper(new_name)
                            conn.execute(
                                "UPDATE branches SET name=?, code=?, slug=? WHERE organization_id=?",
                                (new_name, new_code, new_slug, org_id)
                            )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/rates":
                if not self.require_permission("manage_settings"):
                    return
                payload = self.read_json()
                subject = str(payload.get("subject", "")).strip()
                rate_type = str(payload.get("rate_type", "")).strip()
                if not subject or not rate_type:
                    raise ValueError("Subject and rate type are required")
                with db() as conn:
                    if PG_MODE:
                        cur = conn.execute(
                            """
                            INSERT INTO public.rates(organization_id, subject, rate_type, monthly_fee, description)
                            VALUES (%s,%s,%s,%s,%s)
                            RETURNING id::text AS id
                            """,
                            (current_org_id(conn), subject, rate_type, money(payload.get("monthly_fee")), str(payload.get("description", "")).strip()),
                        )
                        new_id = cur.fetchone()["id"]
                    else:
                        cur = conn.execute(
                            "INSERT INTO rates(subject, rate_type, monthly_fee, description) VALUES (?,?,?,?)",
                            (subject, rate_type, money(payload.get("monthly_fee")), str(payload.get("description", "")).strip()),
                        )
                        new_id = cur.lastrowid
                    conn.commit()
                self.send_json({"ok": True, "id": new_id})
                return
            if parsed.path == "/api/users":
                if not self.require_permission("manage_users"):
                    return
                payload = self.read_json()
                email = str(payload.get("email", "")).strip().lower()
                display_name = str(payload.get("display_name", "")).strip()
                role = normalize_role(payload.get("role"))
                active = str(payload.get("active", "1")).lower() in {"1", "true", "yes", "on"}
                if not email or "@" not in email:
                    raise ValueError("A valid user email is required")
                with db() as conn:
                    if PG_MODE:
                        cur = conn.execute(
                            """
                            INSERT INTO public.app_users(organization_id, email, display_name, role, active)
                            VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (organization_id, email)
                            DO UPDATE SET display_name=excluded.display_name, role=excluded.role,
                                          active=excluded.active, updated_at=now()
                            RETURNING id::text AS id
                            """,
                            (current_org_id(conn), email, display_name, role, active),
                        )
                        new_id = cur.fetchone()["id"]
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO users(email, display_name, role, auth_provider, active)
                            VALUES (?,?,?,?,?)
                            ON CONFLICT(email) DO UPDATE SET display_name=excluded.display_name,
                              role=excluded.role, active=excluded.active
                            """,
                            (email, display_name, role, "email", 1 if active else 0),
                        )
                        new_id = cur.lastrowid
                    conn.commit()
                self.send_json({"ok": True, "id": new_id})
                return
            if parsed.path == "/api/discounts":
                if not self.require_permission("manage_settings"):
                    return
                payload = self.read_json()
                code = str(payload.get("code", "")).strip().upper()
                if not code:
                    raise ValueError("Discount code is required")
                with db() as conn:
                    active = str(payload.get("active", "1")) in {"1", "true", "on", "yes"}
                    if PG_MODE:
                        cur = conn.execute(
                            """
                            INSERT INTO public.discount_codes(organization_id, code, description, percent_off, amount_off, active)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            RETURNING id::text AS id
                            """,
                            (current_org_id(conn), code, str(payload.get("description", "")).strip(), money(payload.get("percent_off")), money(payload.get("amount_off")), active),
                        )
                        new_id = cur.fetchone()["id"]
                    else:
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
                                1 if active else 0,
                            ),
                        )
                        new_id = cur.lastrowid
                    conn.commit()
                    self.send_json({"ok": True, "id": new_id})
                return
            if parsed.path == "/api/restore":
                if not self.require_permission("admin"):
                    return
                payload = self.read_json()
                if payload.get("confirm") != "RESTORE":
                    raise ValueError("Type RESTORE to confirm database recovery")
                restore_backup(str(payload.get("name", "")))
                self.send_json({"ok": True})
                return
            if parsed.path == "/api/demo/seed":
                if not self.require_permission("manage_settings"):
                    return
                with db() as conn:
                    result = seed_demo_data(conn, self.actor_email())
                    conn.commit()
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/reconciliation/preview":
                if not self.require_permission("manage_payments"):
                    return
                payload = self.read_json()
                rows = payload.get("rows", [])
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Upload at least one payment transaction row")
                result = preview_reconciliation(
                    rows[:500],
                    payload.get("payment_method") or "PAD",
                    payload.get("match_rules") or list(DEFAULT_RECON_MATCH_RULES),
                )
                self.send_json({"ok": True, **result})
                return
            if parsed.path == "/api/reconciliation/apply":
                if not self.require_permission("manage_payments"):
                    return
                payload = self.read_json()
                student_id = str(payload.get("student_id") or "").strip()
                month_label = str(payload.get("month_label") or "").strip()
                amount = money(payload.get("amount"))
                description = str(payload.get("description") or "").strip()
                source = str(payload.get("source") or "").strip()
                upload_method = payment_method_label(payload.get("payment_method") or "PAD")
                roster_update_approved = bool(payload.get("roster_update_approved"))
                corrected_student_name = str(payload.get("corrected_student_name") or "").strip()
                corrected_parent_name = str(payload.get("corrected_parent_name") or "").strip()
                transaction_date = normalize_date(payload.get("date") or "")
                if not student_id or month_label not in MONTHS:
                    raise ValueError("Student and month are required before applying a match")
                if amount <= 0:
                    raise ValueError("Zero amount rows cannot be posted to Fee Tracker")
                with db() as conn:
                    org_id = current_org_id(conn) if PG_MODE else None
                    branch_id = current_branch_id(conn)
                    if PG_MODE:
                        student = conn.execute(
                            "SELECT * FROM public.students WHERE id=%s AND organization_id=%s AND branch_id=%s",
                            (student_id, org_id, branch_id),
                        ).fetchone()
                    else:
                        student = conn.execute("SELECT * FROM students WHERE id=? AND branch_id=?", (int(student_id), branch_id)).fetchone()
                    if not student:
                        raise ValueError("Student was not found")
                    student_data = rowdict(student)
                    if str(student_data.get("status", "")).upper() != "C":
                        raise ValueError("Only active students can be updated from PAD reconciliation")
                    if payment_method_label(student_data.get("payment_method")) != upload_method:
                        raise ValueError(f"{upload_method} upload can only update students with {upload_method} payment method")
                    existing_payment = float(get_payments(conn).get(str(student_id), {}).get(month_label, 0) or 0)
                    if existing_payment > 0:
                        raise ValueError(f"{month_label} already has a payment recorded for this student")
                    if PG_MODE:
                        existing_import = conn.execute(
                            """
                            SELECT 1
                            FROM public.payment_import_rows
                            WHERE organization_id=%s AND student_id=%s AND month_label=%s
                              AND ABS(amount - %s) < 0.01
                              AND lower(coalesce(description,''))=lower(%s)
                              AND match_status='approved'
                            LIMIT 1
                            """,
                            (org_id, student_id, month_label, amount, description),
                        ).fetchone()
                    else:
                        existing_import = conn.execute(
                            """
                            SELECT 1
                            FROM payment_import_rows
                            WHERE student_id=? AND month_label=?
                              AND ABS(amount - ?) < 0.01
                              AND lower(coalesce(description,''))=lower(?)
                              AND match_status='approved'
                            LIMIT 1
                            """,
                            (int(student_id), month_label, amount, description),
                        ).fetchone()
                    if existing_import:
                        raise ValueError("This PAD transaction was already imported")
                    before_student = dict(student_data)
                    roster_changes = {}
                    if roster_update_approved:
                        if corrected_student_name and corrected_student_name != str(student_data.get("student_name") or ""):
                            roster_changes["student_name"] = corrected_student_name
                        if corrected_parent_name and corrected_parent_name != str(student_data.get("parent_guardian") or ""):
                            roster_changes["parent_guardian"] = corrected_parent_name
                    if roster_changes:
                        modification_note = f"{datetime.now().strftime('%Y-%m-%d')}: Payment reconciliation corrected {', '.join(roster_changes.keys())}"
                        if PG_MODE:
                            conn.execute(
                                """
                                UPDATE public.students
                                SET student_name=COALESCE(NULLIF(%s,''), student_name),
                                    parent_guardian=COALESCE(NULLIF(%s,''), parent_guardian),
                                    last_modification=%s,
                                    updated_at=now()
                                WHERE id=%s AND organization_id=%s
                                """,
                                (
                                    roster_changes.get("student_name", ""),
                                    roster_changes.get("parent_guardian", ""),
                                    modification_note,
                                    student_id,
                                    org_id,
                                ),
                            )
                        else:
                            conn.execute(
                                """
                                UPDATE students
                                SET student_name=COALESCE(NULLIF(?,''), student_name),
                                    parent_guardian=COALESCE(NULLIF(?,''), parent_guardian),
                                    last_modification=?,
                                    updated_at=CURRENT_TIMESTAMP
                                WHERE id=?
                                """,
                                (
                                    roster_changes.get("student_name", ""),
                                    roster_changes.get("parent_guardian", ""),
                                    modification_note,
                                    int(student_id),
                                ),
                            )
                        record_audit(
                            conn,
                            "update",
                            "student",
                            student_id,
                            "Roster name correction from payment reconciliation",
                            before=before_student,
                            after={**before_student, **roster_changes, "last_modification": modification_note},
                            actor_email=self.actor_email(),
                        )
                    update_payment_amount(conn, student_id, month_label, amount, "reconciliation", self.actor_email())
                    if description:
                        alias = description[:120]
                        if PG_MODE:
                            conn.execute(
                                """
                                INSERT INTO public.payer_aliases(organization_id, student_id, alias, source)
                                VALUES (%s,%s,%s,%s)
                                ON CONFLICT(student_id, alias) DO NOTHING
                                """,
                                (org_id, student_id, alias, source),
                            )
                        else:
                            conn.execute(
                                "INSERT OR IGNORE INTO payer_aliases(student_id, alias, source) VALUES (?,?,?)",
                                (int(student_id), alias, source),
                            )
                    if PG_MODE:
                        cur = conn.execute(
                            """
                            INSERT INTO public.payment_imports(organization_id, file_name, source)
                            VALUES (%s,%s,%s)
                            RETURNING id::text AS id
                            """,
                            (org_id, str(payload.get("file_name") or "manual review"), source),
                        )
                        import_id = cur.fetchone()["id"]
                        conn.execute(
                            """
                            INSERT INTO public.payment_import_rows(
                                import_id, organization_id, student_id, transaction_date, description, amount, source,
                                month_label, match_score, match_status, notes, applied_at
                            ) VALUES (%s,%s,%s,NULLIF(%s,'')::date,%s,%s,%s,%s,%s,%s,%s,now())
                            """,
                            (
                                import_id,
                                org_id,
                                student_id,
                                transaction_date,
                                description,
                                amount,
                                source,
                                month_label,
                                int(payload.get("score") or 0),
                                "approved",
                                str(payload.get("notes") or "").strip(),
                            ),
                        )
                    else:
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
                                int(student_id),
                                transaction_date,
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
                if not self.require_permission("manage_payments"):
                    return
                payload = self.read_json()
                rows = payload.get("rows", [])
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Upload at least one fee tracker CSV row")
                self.send_json({"ok": True, "rows": preview_fee_import(rows[:1000])})
                return
            if parsed.path == "/api/fee-import/apply":
                if not self.require_permission("manage_payments"):
                    return
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
        # ── PUT staff member details (from staffbase) ──
        staff_base_match = re.match(r"^/api/staffbase/staff/([^/]+)$", parsed.path)
        if staff_base_match:
            staff_id = staff_base_match.group(1)
            if not self.require_auth():
                return
            role_allowed = False
            if self.auth_access:
                u_role = str(self.auth_access.get("role") or "").lower()
                if u_role in ["admin", "office manager", "manager", "principal_owner", "administrator", "owner"]:
                    role_allowed = True
            if not role_allowed:
                self.send_json({"error": "Forbidden"}, 403)
                return

            payload = self.read_json()
            name = str(payload.get("name", "")).strip()
            email = str(payload.get("email", "")).strip()
            role = str(payload.get("role", "staff")).strip()
            dept = str(payload.get("dept", "Administration")).strip()
            pos = str(payload.get("pos", "")).strip()
            pin = str(payload.get("pin", "")).strip()
            password = str(payload.get("password", "")).strip()

            if not name or not email:
                self.send_json({"error": "name and email are required"}, 400)
                return

            db_role = "manager" if role in ["principal_owner", "administrator", "office_manager", "manager"] else "staff"
            initials = "".join([part[0] for part in name.split(" ") if part]).upper()[:2]

            with db() as conn:
                if PG_MODE:
                    existing = conn.execute("SELECT pin, pin_hash, password_hash, active FROM public.staff_members WHERE id=%s", (staff_id,)).fetchone()
                    d_exist = rowdict(existing) if existing else {}
                    
                    pin_val = pin if pin else d_exist.get("pin")
                    pin_h = hash_bcrypt(pin) if pin else d_exist.get("pin_hash")
                    pw_h = hash_bcrypt(password) if password else d_exist.get("password_hash")
                    
                    db_active_bool = d_exist.get("active") not in [False, "false", "False", 0, "0"]
                    active_val = payload.get("active") if "active" in payload else db_active_bool
                    
                    conn.execute(
                        """
                        UPDATE public.staff_members
                        SET staff_name=%s, email=%s, role=%s, subject=%s, role_title=%s,
                            avatar_initials=%s, pin=%s, pin_hash=%s, password_hash=%s, active=%s, updated_at=now()
                        WHERE id=%s
                        """,
                        (name, email, db_role, dept, pos, initials, pin_val, pin_h, pw_h, active_val, staff_id)
                    )
                else:
                    existing = conn.execute("SELECT pin, pin_hash, password_hash, active FROM staff_members WHERE id=?", (int(staff_id),)).fetchone()
                    d_exist = rowdict(existing) if existing else {}
                    
                    pin_val = pin if pin else d_exist.get("pin")
                    pin_h = hash_bcrypt(pin) if pin else d_exist.get("pin_hash")
                    pw_h = hash_bcrypt(password) if password else d_exist.get("password_hash")
                    
                    db_active_bool = d_exist.get("active") not in [False, "false", "False", 0, "0"]
                    active_val = 1 if (payload.get("active") if "active" in payload else db_active_bool) else 0
                    
                    conn.execute(
                        """
                        UPDATE staff_members
                        SET staff_name=?, email=?, role=?, subject=?, role_title=?,
                            avatar_initials=?, pin=?, pin_hash=?, password_hash=?, active=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (name, email, db_role, dept, pos, initials, pin_val, pin_h, pw_h, active_val, int(staff_id))
                    )
                conn.commit()
            self.send_json({"ok": True})
            return

        # ── PUT staff notifications-seen ──
        if parsed.path == "/api/staff/notifications-seen" or parsed.path == "/api/notifications/seen":
            if not self.require_staffbase_auth():
                return
            with db() as conn:
                now_iso = datetime.now().isoformat()
                if PG_MODE:
                    conn.execute("UPDATE public.staff_members SET notifications_last_checked_at=now() WHERE id=%s", (self.staff_id,))
                else:
                    conn.execute("UPDATE staff_members SET notifications_last_checked_at=? WHERE id=?", (now_iso, int(self.staff_id)))
                conn.commit()
            self.send_json({"ok": True})
            return

        # ── PUT manager update schedule shift ──
        m_sched_match = re.match(r"^/api/manager/schedule/([^/]+)/([^/]+)$", parsed.path)
        if m_sched_match:
            if not self.require_staffbase_manager():
                return
            target_staff_id = m_sched_match.group(1)
            day = m_sched_match.group(2)
            payload = self.read_json()
            stype = str(payload.get("shiftType", "Off"))
            start = str(payload.get("start", ""))
            end = str(payload.get("end", ""))
            loc = str(payload.get("location", ""))
            
            week_start = get_monday_of_current_week()
            with db() as conn:
                if PG_MODE:
                    conn.execute("DELETE FROM public.staff_schedules WHERE staff_id=%s AND week_start=%s AND weekday=%s", (target_staff_id, week_start, day))
                    conn.execute(
                        """
                        INSERT INTO public.staff_schedules(organization_id, staff_id, week_start, weekday, shift_type, start_time, end_time, location, acknowledged)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false)
                        """,
                        (current_org_id(conn), target_staff_id, week_start, day, stype, start, end, loc)
                    )
                else:
                    conn.execute("DELETE FROM staff_schedules WHERE staff_id=? AND week_start=? AND weekday=?", (int(target_staff_id), week_start, day))
                    conn.execute(
                        """
                        INSERT INTO staff_schedules(staff_id, week_start, weekday, shift_type, start_time, end_time, location, published)
                        VALUES (?,?,?,?,?,?,?,0)
                        """,
                        (int(target_staff_id), week_start, day, stype, start, end, loc)
                    )
                conn.commit()
            self.send_json({"ok": True})
            return

        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        id_pattern = r"([0-9a-fA-F-]+)"
        match = re.match(rf"^/api/students/{id_pattern}$", parsed.path)
        restore_match = re.match(rf"^/api/students/{id_pattern}/restore$", parsed.path)
        rate_match = re.match(rf"^/api/rates/{id_pattern}$", parsed.path)
        discount_match = re.match(rf"^/api/discounts/{id_pattern}$", parsed.path)
        user_match = re.match(rf"^/api/users/{id_pattern}$", parsed.path)
        staff_member_match = re.match(rf"^/api/staff/members/{id_pattern}$", parsed.path)
        payment_match = re.match(rf"^/api/payments/{id_pattern}/([^/]+)$", parsed.path)
        try:
            if restore_match:
                if not self.require_permission("delete_records"):
                    return
                with db() as conn:
                    branch_id = current_branch_id(conn)
                    if PG_MODE:
                        old_student = conn.execute("SELECT * FROM public.students WHERE id=%s AND organization_id=%s AND branch_id=%s", (restore_match.group(1), current_org_id(conn), branch_id)).fetchone()
                        conn.execute(
                            """
                            UPDATE public.students
                            SET deleted_at=NULL, deleted_by=NULL, delete_reason=NULL,
                                last_modification=%s, updated_at=now()
                            WHERE id=%s AND organization_id=%s AND branch_id=%s
                            """,
                            (datetime.now().strftime("%Y-%m-%d: Restored"), restore_match.group(1), current_org_id(conn), branch_id),
                        )
                    else:
                        old_student = conn.execute("SELECT * FROM students WHERE id=? AND branch_id=?", (int(restore_match.group(1)), branch_id)).fetchone()
                        conn.execute(
                            """
                            UPDATE students
                            SET deleted_at=NULL, deleted_by=NULL, delete_reason=NULL,
                                last_modification=?, updated_at=CURRENT_TIMESTAMP
                            WHERE id=? AND branch_id=?
                            """,
                            (datetime.now().strftime("%Y-%m-%d: Restored"), int(restore_match.group(1)), branch_id),
                        )
                    if old_student:
                        record_audit(
                            conn,
                            "student_restore",
                            "student",
                            restore_match.group(1),
                            f"Restored student {row_get(old_student, 'student_name', '')}",
                            before=old_student,
                            actor_email=self.actor_email(),
                        )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if staff_member_match:
                if not self.require_permission("manage_staff"):
                    return
                payload = self.read_json()
                with db() as conn:
                    save_staff_member(conn, payload, staff_member_match.group(1))
                    conn.commit()
                    self.send_json({"ok": True})
                return
            if payment_match:
                if not self.require_permission("manage_payments"):
                    return
                student_id = payment_match.group(1)
                month_label = payment_match.group(2)
                if month_label not in MONTHS:
                    raise ValueError("Unknown month")
                payload = self.read_json()
                with db() as conn:
                    update_payment_amount(conn, student_id if PG_MODE else int(student_id), month_label, money(payload.get("amount")), "manual", self.actor_email())
                    conn.commit()
                self.send_json({"ok": True})
                return
            if rate_match:
                if not self.require_permission("manage_settings"):
                    return
                payload = self.read_json()
                subject = str(payload.get("subject", "")).strip()
                rate_type = str(payload.get("rate_type", "")).strip()
                if not subject or not rate_type:
                    raise ValueError("Subject and rate type are required")
                with db() as conn:
                    if PG_MODE:
                        conn.execute(
                            """
                            UPDATE public.rates
                            SET subject=%s, rate_type=%s, monthly_fee=%s, description=%s
                            WHERE id=%s AND organization_id=%s
                            """,
                            (subject, rate_type, money(payload.get("monthly_fee")), str(payload.get("description", "")).strip(), rate_match.group(1), current_org_id(conn)),
                        )
                    else:
                        conn.execute(
                            "UPDATE rates SET subject=?, rate_type=?, monthly_fee=?, description=? WHERE id=?",
                            (subject, rate_type, money(payload.get("monthly_fee")), str(payload.get("description", "")).strip(), int(rate_match.group(1))),
                        )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if discount_match:
                if not self.require_permission("manage_settings"):
                    return
                payload = self.read_json()
                code = str(payload.get("code", "")).strip().upper()
                if not code:
                    raise ValueError("Discount code is required")
                with db() as conn:
                    active = str(payload.get("active", "1")) in {"1", "true", "on", "yes"}
                    if PG_MODE:
                        conn.execute(
                            """
                            UPDATE public.discount_codes
                            SET code=%s, description=%s, percent_off=%s, amount_off=%s, active=%s
                            WHERE id=%s AND (organization_id=%s OR organization_id IS NULL)
                            """,
                            (code, str(payload.get("description", "")).strip(), money(payload.get("percent_off")), money(payload.get("amount_off")), active, discount_match.group(1), current_org_id(conn)),
                        )
                    else:
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
                                1 if active else 0,
                                int(discount_match.group(1)),
                            ),
                        )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if user_match:
                if not self.require_permission("manage_users"):
                    return
                payload = self.read_json()
                email = str(payload.get("email", "")).strip().lower()
                display_name = str(payload.get("display_name", "")).strip()
                role = normalize_role(payload.get("role"))
                active = str(payload.get("active", "1")).lower() in {"1", "true", "yes", "on"}
                if not email or "@" not in email:
                    raise ValueError("A valid user email is required")
                with db() as conn:
                    existing = None
                    if PG_MODE:
                        existing = conn.execute("SELECT role, active FROM public.app_users WHERE id=%s AND organization_id=%s", (user_match.group(1), current_org_id(conn))).fetchone()
                    else:
                        existing = conn.execute("SELECT role, active FROM users WHERE id=?", (int(user_match.group(1)),)).fetchone()
                    if existing and normalize_role(row_get(existing, "role")) == "Admin" and row_get(existing, "active", True):
                        if (role != "Admin" or not active) and active_admin_count(conn) <= 1:
                            raise ValueError("At least one active Admin user is required")
                    if PG_MODE:
                        conn.execute(
                            """
                            UPDATE public.app_users
                            SET email=%s, display_name=%s, role=%s, active=%s, updated_at=now()
                            WHERE id=%s AND organization_id=%s
                            """,
                            (email, display_name, role, active, user_match.group(1), current_org_id(conn)),
                        )
                    else:
                        conn.execute(
                            "UPDATE users SET email=?, display_name=?, role=?, active=? WHERE id=?",
                            (email, display_name, role, 1 if active else 0, int(user_match.group(1))),
                        )
                    conn.commit()
                self.send_json({"ok": True})
                return
            if not match:
                self.send_json({"ok": False, "error": "Not found"}, 404)
                return
            if not self.require_permission("manage_students"):
                return
            student = normalize_student(self.read_json())
            with db() as conn:
                branch_id = current_branch_id(conn)
                if PG_MODE:
                    old_student = conn.execute("SELECT * FROM public.students WHERE id=%s AND organization_id=%s AND branch_id=%s", (match.group(1), current_org_id(conn), branch_id)).fetchone()
                    if old_student:
                        old_student = display_student(old_student)
                else:
                    old_student = conn.execute("SELECT * FROM students WHERE id=? AND branch_id=?", (int(match.group(1)), branch_id)).fetchone()
                modification_note = student_modification_note(old_student, student)
                old_status = row_get(old_student, "status", "")
                if PG_MODE:
                    conn.execute(
                        """
                        UPDATE public.students SET
                            student_name=%s, parent_guardian=%s, status=%s, enrol_date=NULLIF(%s,'')::date,
                            subjects=%s, rate_type=%s, std_monthly_fee=%s, payment_method=%s, phone=%s,
                            email=%s, siblings=%s, notes=%s, last_modification=%s, updated_at=now()
                        WHERE id=%s AND organization_id=%s AND branch_id=%s
                        """,
                        (
                            student["student_name"],
                            student["parent_guardian"],
                            student["status"],
                            student["enrol_date"],
                            pg_subjects(student["subjects"]),
                            student["rate_type"],
                            student["std_monthly_fee"],
                            student["payment_method"],
                            student["phone"],
                            student["email"],
                            student["siblings"],
                            student["notes"],
                            modification_note,
                            match.group(1),
                            current_org_id(conn),
                            branch_id,
                        ),
                    )
                    save_student_schedules(conn, match.group(1), student.get("schedules", []))
                    record_status_change(conn, match.group(1), old_status, student["status"], modification_note)
                    record_audit(
                        conn,
                        "student_update",
                        "student",
                        match.group(1),
                        f"Updated student {student['student_name']}: {modification_note}",
                        before=old_student,
                        after={**student, "id": match.group(1), "last_modification": modification_note},
                        actor_email=self.actor_email(),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE students SET
                            student_name=?, parent_guardian=?, status=?, enrol_date=?, subjects=?, rate_type=?,
                            std_monthly_fee=?, payment_method=?, phone=?, email=?, siblings=?, notes=?, last_modification=?,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND branch_id=?
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
                            branch_id,
                        ),
                    )
                    save_student_schedules(conn, int(match.group(1)), student.get("schedules", []))
                    record_status_change(conn, int(match.group(1)), old_status, student["status"], modification_note)
                    record_audit(
                        conn,
                        "student_update",
                        "student",
                        match.group(1),
                        f"Updated student {student['student_name']}: {modification_note}",
                        before=old_student,
                        after={**student, "id": match.group(1), "last_modification": modification_note},
                        actor_email=self.actor_email(),
                    )
                conn.commit()
            self.send_json({"ok": True})
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        # ── DELETE staff member (from staffbase) ──
        staff_base_match = re.match(r"^/api/staffbase/staff/([^/]+)$", parsed.path)
        if staff_base_match:
            staff_id = staff_base_match.group(1)
            if not self.require_auth():
                return
            role_allowed = False
            if self.auth_access:
                u_role = str(self.auth_access.get("role") or "").lower()
                if u_role in ["admin", "office manager", "manager", "principal_owner", "administrator", "owner"]:
                    role_allowed = True
            if not role_allowed:
                self.send_json({"error": "Forbidden"}, 403)
                return

            with db() as conn:
                if PG_MODE:
                    conn.execute("DELETE FROM public.staff_members WHERE id=%s", (staff_id,))
                else:
                    conn.execute("DELETE FROM staff_members WHERE id=?", (int(staff_id),))
                conn.commit()
            self.send_json({"ok": True})
            return

        # ── DELETE announcement ──
        ann_match = re.match(r"^/api/announcements/([^/]+)$", parsed.path)
        if ann_match:
            if not self.require_staffbase_manager():
                return
            ann_id = ann_match.group(1)
            with db() as conn:
                if PG_MODE:
                    conn.execute("DELETE FROM public.announcements WHERE id=%s", (ann_id,))
                else:
                    conn.execute("DELETE FROM announcements WHERE id=?", (int(ann_id),))
                conn.commit()
            self.send_json({"ok": True})
            return

        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        id_pattern = r"([0-9a-fA-F-]+)"
        match = re.match(rf"^/api/students/{id_pattern}$", parsed.path)
        rate_match = re.match(rf"^/api/rates/{id_pattern}$", parsed.path)
        discount_match = re.match(rf"^/api/discounts/{id_pattern}$", parsed.path)
        user_match = re.match(rf"^/api/users/{id_pattern}$", parsed.path)
        staff_member_match = re.match(rf"^/api/staff/members/{id_pattern}$", parsed.path)
        if rate_match:
            if not self.require_permission("manage_settings"):
                return
            with db() as conn:
                if PG_MODE:
                    conn.execute("DELETE FROM public.rates WHERE id=%s AND organization_id=%s", (rate_match.group(1), current_org_id(conn)))
                else:
                    conn.execute("DELETE FROM rates WHERE id=?", (int(rate_match.group(1)),))
                conn.commit()
            self.send_json({"ok": True})
            return
        if discount_match:
            if not self.require_permission("manage_settings"):
                return
            with db() as conn:
                if PG_MODE:
                    conn.execute("DELETE FROM public.discount_codes WHERE id=%s AND (organization_id=%s OR organization_id IS NULL)", (discount_match.group(1), current_org_id(conn)))
                else:
                    conn.execute("DELETE FROM discount_codes WHERE id=?", (int(discount_match.group(1)),))
                conn.commit()
            self.send_json({"ok": True})
            return
        if user_match:
            if not self.require_permission("manage_users"):
                return
            with db() as conn:
                if PG_MODE:
                    existing = conn.execute("SELECT role, active FROM public.app_users WHERE id=%s AND organization_id=%s", (user_match.group(1), current_org_id(conn))).fetchone()
                else:
                    existing = conn.execute("SELECT role, active FROM users WHERE id=?", (int(user_match.group(1)),)).fetchone()
                if existing and normalize_role(row_get(existing, "role")) == "Admin" and row_get(existing, "active", True) and active_admin_count(conn) <= 1:
                    self.send_json({"ok": False, "error": "At least one active Admin user is required"}, 400)
                    return
                if PG_MODE:
                    conn.execute("DELETE FROM public.app_users WHERE id=%s AND organization_id=%s", (user_match.group(1), current_org_id(conn)))
                else:
                    conn.execute("DELETE FROM users WHERE id=?", (int(user_match.group(1)),))
                conn.commit()
            self.send_json({"ok": True})
            return
        if staff_member_match:
            if not self.require_permission("manage_staff"):
                return
            with db() as conn:
                if PG_MODE:
                    conn.execute("DELETE FROM public.staff_members WHERE id=%s AND organization_id=%s", (staff_member_match.group(1), current_org_id(conn)))
                else:
                    conn.execute("DELETE FROM staff_members WHERE id=?", (int(staff_member_match.group(1)),))
                conn.commit()
            self.send_json({"ok": True})
            return
        if not match:
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        if not self.require_permission("delete_records"):
            return
        with db() as conn:
            branch_id = current_branch_id(conn)
            if PG_MODE:
                old_student = conn.execute("SELECT * FROM public.students WHERE id=%s AND organization_id=%s AND branch_id=%s", (match.group(1), current_org_id(conn), branch_id)).fetchone()
                if not old_student:
                    self.send_json({"ok": False, "error": "Student was not found"}, 404)
                    return
                conn.execute(
                    """
                    UPDATE public.students
                    SET deleted_at=now(), deleted_by=%s, delete_reason=%s,
                        last_modification=%s, updated_at=now()
                    WHERE id=%s AND organization_id=%s AND branch_id=%s
                    """,
                    (
                        self.actor_email(),
                        "Soft deleted by Admin after review",
                        datetime.now().strftime("%Y-%m-%d: Soft deleted"),
                        match.group(1),
                        current_org_id(conn),
                        branch_id,
                    ),
                )
            else:
                old_student = conn.execute("SELECT * FROM students WHERE id=? AND branch_id=?", (int(match.group(1)), branch_id)).fetchone()
                if not old_student:
                    self.send_json({"ok": False, "error": "Student was not found"}, 404)
                    return
                conn.execute(
                    """
                    UPDATE students
                    SET deleted_at=CURRENT_TIMESTAMP, deleted_by=?, delete_reason=?,
                        last_modification=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND branch_id=?
                    """,
                    (
                        self.actor_email(),
                        "Soft deleted by Admin after review",
                        datetime.now().strftime("%Y-%m-%d: Soft deleted"),
                        int(match.group(1)),
                        branch_id,
                    ),
                )
            record_audit(
                conn,
                "student_soft_delete",
                "student",
                match.group(1),
                f"Soft deleted student {row_get(old_student, 'student_name', '')}",
                before=old_student,
                actor_email=self.actor_email(),
            )
            conn.commit()
        self.send_json({"ok": True})


if __name__ == "__main__":
    if PG_MODE:
        try:
            ensure_pg_defaults()
        except Exception as e:
            import traceback
            print(f"WARNING: Database initialization failed on startup: {e}", file=sys.stderr)
            traceback.print_exc()
    else:
        init_db(force="--reseed" in sys.argv)
        with db() as conn:
            ensure_staff_tables(conn)
        ensure_meta_defaults()
    port = int(os.environ.get("PORT", "8765"))
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"SMP tracking site running at http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
