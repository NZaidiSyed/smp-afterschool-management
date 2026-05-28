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
from urllib.parse import quote, unquote, urlparse, urlunparse
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

BASE_MONTHS = MONTHS[:]
ROLE_OPTIONS = ["Admin", "Office Manager", "Office Assistant"]
ROLE_PERMISSIONS = {
    "Admin": {"admin", "manage_students", "manage_payments", "manage_settings", "manage_users", "delete_records"},
    "Office Manager": {"manage_students", "manage_payments"},
    "Office Assistant": {"manage_payments"},
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
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y", "%b %d %Y", "%d %b %Y", "%Y/%m/%d"]:
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
    row = conn.execute("SELECT id::text AS id FROM public.organizations ORDER BY created_at LIMIT 1").fetchone()
    if row:
        SMP_ORGANIZATION_ID = row["id"]
        return SMP_ORGANIZATION_ID
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


def normalize_role(role):
    value = str(role or "").strip()
    aliases = {
        "admin": "Admin",
        "owner": "Admin",
        "office manager": "Office Manager",
        "manager": "Office Manager",
        "office assistant": "Office Assistant",
        "assistant": "Office Assistant",
        "staff": "Office Assistant",
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
            SELECT name, phone, details, subjects_offered, current_month
            FROM public.organizations
            WHERE id=%s
            """,
            (org_id,),
        ).fetchone()
        values = dict(DEFAULT_SETTINGS)
        if row:
            values.update(
                {
                    "institution_name": row.get("name") or DEFAULT_SETTINGS["institution_name"],
                    "institution_phone": row.get("phone") or "",
                    "institution_details": row.get("details") or DEFAULT_SETTINGS["institution_details"],
                    "subjects_offered": "\n".join(row.get("subjects_offered") or ["Math", "English"]),
                    "current_month": row.get("current_month") or DEFAULT_SETTINGS["current_month"],
                }
            )
        if month_position(values.get("current_month")) < month_position(current_month_label()):
            values["current_month"] = current_month_label()
        return values
    rows = conn.execute("SELECT key, value FROM app_meta").fetchall()
    values = {row["key"]: row["value"] for row in rows}
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
    for alias in aliases.get(str(student["id"]), []):
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
    prev_paid = float(payments.get(str(student["id"]), {}).get(prev_month, 0) or 0) if prev_month else 0
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
        if PG_MODE:
            students = get_students(conn)
            alias_rows = conn.execute(
                "SELECT student_id::text AS student_id, alias FROM public.payer_aliases WHERE organization_id=%s",
                (current_org_id(conn),),
            ).fetchall()
        else:
            alias_rows = conn.execute("SELECT student_id, alias FROM payer_aliases").fetchall()
    aliases = {}
    for row in alias_rows:
        aliases.setdefault(str(row["student_id"]), []).append(row["alias"])

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


def get_students(conn):
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT id::text AS id, number, student_name, parent_guardian, status, enrol_date,
                   subjects, rate_type, std_monthly_fee, payment_method, phone, email, siblings,
                   notes, last_modification, created_at, updated_at
            FROM public.students
            WHERE organization_id=%s
            ORDER BY number, student_name
            """,
            (current_org_id(conn),),
        ).fetchall()
        return [display_student(row) for row in rows]
    return [rowdict(row) for row in conn.execute("SELECT * FROM students ORDER BY number, student_name")]


def get_payments(conn):
    if PG_MODE:
        rows = conn.execute(
            """
            SELECT student_id::text AS student_id, month_label, amount
            FROM public.payments
            WHERE organization_id=%s
            """,
            (current_org_id(conn),),
        ).fetchall()
    else:
        rows = conn.execute("SELECT student_id, month_label, amount FROM payments").fetchall()
    by_student = {}
    for row in rows:
        by_student.setdefault(str(row["student_id"]), {})[row["month_label"]] = float(row["amount"] or 0)
    return by_student


def fee_tracker(conn):
    payments = get_payments(conn)
    rows = []
    for student in get_students(conn):
        month_values = {month: payments.get(str(student["id"]), {}).get(month, 0) for month in MONTHS}
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
        method = payment_method_label(row["payment_method"])
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


def insert_student_record(conn, student, number):
    note = datetime.now().strftime("%Y-%m-%d: Created")
    if PG_MODE:
        org_id = current_org_id(conn)
        row = conn.execute(
            """
            INSERT INTO public.students (
                organization_id, number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
                std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
            ) VALUES (%s,%s,%s,%s,%s,NULLIF(%s,'')::date,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id::text AS id
            """,
            (
                org_id,
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
        execute_many(
            conn,
            """
            INSERT INTO public.payments(organization_id, student_id, month_label, amount)
            VALUES (?,?,?,0)
            ON CONFLICT(student_id, month_label) DO NOTHING
            """,
            [(org_id, student_id, month) for month in MONTHS],
        )
        return student_id
    cur = conn.execute(
        """
        INSERT INTO students (
            number, student_name, parent_guardian, status, enrol_date, subjects, rate_type,
            std_monthly_fee, payment_method, phone, email, siblings, notes, last_modification
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
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
    for month in MONTHS:
        conn.execute("INSERT INTO payments(student_id, month_label, amount) VALUES (?,?,0)", (cur.lastrowid, month))
    return cur.lastrowid


def next_student_number(conn):
    if PG_MODE:
        row = conn.execute("SELECT COALESCE(MAX(number),0)+1 AS n FROM public.students WHERE organization_id=%s", (current_org_id(conn),)).fetchone()
    else:
        row = conn.execute("SELECT COALESCE(MAX(number),0)+1 AS n FROM students").fetchone()
    return int(row["n"] or 1)


def update_payment_amount(conn, student_id, month_label, amount, source="manual"):
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


def ensure_pg_defaults():
    with db() as conn:
        org_id = current_org_id(conn)
        ensure_access_tables(conn)
        for subject in ["Math", "English"]:
            conn.execute(
                """
                INSERT INTO public.rates(organization_id, subject, rate_type, monthly_fee, description)
                VALUES (%s,%s,'Regular',165,'Default starter rate')
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
        if not SUPABASE_REQUIRE_AUTH:
            self.auth_access = {"role": "Admin", "email": "admin@local.smp", "display_name": "Local Admin"}
            return True
        if not SUPABASE_URL or not SUPABASE_ANON_KEY:
            self.send_json({"ok": False, "error": "Supabase auth is not configured on the server"}, 503)
            return False
        token = self.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
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

    def require_permission(self, permission):
        role = normalize_role((self.auth_access or {}).get("role"))
        if permission not in ROLE_PERMISSIONS.get(role, set()):
            self.send_json({"ok": False, "error": f"{role or 'This user'} does not have permission for this action"}, 403)
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
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
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        if parsed.path == "/api/bootstrap":
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
                    backups = []
                else:
                    rates = [rowdict(row) for row in conn.execute("SELECT * FROM rates ORDER BY subject, rate_type")]
                    users = list_app_users(conn)
                    subscriptions = [rowdict(row) for row in conn.execute("SELECT * FROM subscriptions ORDER BY id DESC")]
                    discount_codes = [rowdict(row) for row in conn.execute("SELECT * FROM discount_codes ORDER BY active DESC, code")]
                    payer_aliases = [rowdict(row) for row in conn.execute("SELECT * FROM payer_aliases ORDER BY alias")]
                    backups = list_backups()
                self.send_json(
                    {
                        "students": get_students(conn),
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
                        "current_user": self.auth_access or {},
                        "role_options": ROLE_OPTIONS,
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
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        try:
            if parsed.path == "/api/students":
                if not self.require_permission("manage_students"):
                    return
                student = normalize_student(self.read_json())
                with db() as conn:
                    new_id = insert_student_record(conn, student, next_student_number(conn))
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
                            insert_student_record(conn, student, next_number)
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
                with db() as conn:
                    if PG_MODE:
                        conn.execute(
                            """
                            UPDATE public.organizations
                            SET name=%s, phone=%s, details=%s, subjects_offered=%s, current_month=%s, updated_at=now()
                            WHERE id=%s
                            """,
                            (
                                str(payload.get("institution_name", DEFAULT_SETTINGS["institution_name"])),
                                str(payload.get("institution_phone", "")),
                                str(payload.get("institution_details", DEFAULT_SETTINGS["institution_details"])),
                                configured_subjects({"subjects_offered": str(payload.get("subjects_offered", DEFAULT_SETTINGS["subjects_offered"]))}),
                                str(payload.get("current_month", DEFAULT_SETTINGS["current_month"])),
                                current_org_id(conn),
                            ),
                        )
                    else:
                        for key in DEFAULT_SETTINGS:
                            if key in payload:
                                conn.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES (?,?)", (key, str(payload.get(key, ""))))
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
            if parsed.path == "/api/reconciliation/preview":
                if not self.require_permission("manage_payments"):
                    return
                payload = self.read_json()
                rows = payload.get("rows", [])
                if not isinstance(rows, list) or not rows:
                    raise ValueError("Upload at least one payment transaction row")
                self.send_json({"ok": True, "rows": preview_reconciliation(rows[:500])})
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
                if not student_id or month_label not in MONTHS:
                    raise ValueError("Student and month are required before applying a match")
                with db() as conn:
                    org_id = current_org_id(conn) if PG_MODE else None
                    if PG_MODE:
                        student = conn.execute("SELECT id::text AS id FROM public.students WHERE id=%s AND organization_id=%s", (student_id, org_id)).fetchone()
                    else:
                        student = conn.execute("SELECT * FROM students WHERE id=?", (int(student_id),)).fetchone()
                    if not student:
                        raise ValueError("Student was not found")
                    update_payment_amount(conn, student_id, month_label, amount, "reconciliation")
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
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        id_pattern = r"([0-9a-fA-F-]+)"
        match = re.match(rf"^/api/students/{id_pattern}$", parsed.path)
        rate_match = re.match(rf"^/api/rates/{id_pattern}$", parsed.path)
        discount_match = re.match(rf"^/api/discounts/{id_pattern}$", parsed.path)
        user_match = re.match(rf"^/api/users/{id_pattern}$", parsed.path)
        payment_match = re.match(rf"^/api/payments/{id_pattern}/([^/]+)$", parsed.path)
        try:
            if payment_match:
                if not self.require_permission("manage_payments"):
                    return
                student_id = payment_match.group(1)
                month_label = payment_match.group(2)
                if month_label not in MONTHS:
                    raise ValueError("Unknown month")
                payload = self.read_json()
                with db() as conn:
                    update_payment_amount(conn, student_id if PG_MODE else int(student_id), month_label, money(payload.get("amount")))
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
                if PG_MODE:
                    old_student = conn.execute("SELECT * FROM public.students WHERE id=%s AND organization_id=%s", (match.group(1), current_org_id(conn))).fetchone()
                    if old_student:
                        old_student = display_student(old_student)
                else:
                    old_student = conn.execute("SELECT * FROM students WHERE id=?", (int(match.group(1)),)).fetchone()
                modification_note = student_modification_note(old_student, student)
                if PG_MODE:
                    conn.execute(
                        """
                        UPDATE public.students SET
                            student_name=%s, parent_guardian=%s, status=%s, enrol_date=NULLIF(%s,'')::date,
                            subjects=%s, rate_type=%s, std_monthly_fee=%s, payment_method=%s, phone=%s,
                            email=%s, siblings=%s, notes=%s, last_modification=%s, updated_at=now()
                        WHERE id=%s AND organization_id=%s
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
                        ),
                    )
                else:
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
        if parsed.path.startswith("/api/") and not self.require_auth():
            return
        id_pattern = r"([0-9a-fA-F-]+)"
        match = re.match(rf"^/api/students/{id_pattern}$", parsed.path)
        rate_match = re.match(rf"^/api/rates/{id_pattern}$", parsed.path)
        discount_match = re.match(rf"^/api/discounts/{id_pattern}$", parsed.path)
        user_match = re.match(rf"^/api/users/{id_pattern}$", parsed.path)
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
        if not match:
            self.send_json({"ok": False, "error": "Not found"}, 404)
            return
        if not self.require_permission("delete_records"):
            return
        with db() as conn:
            if PG_MODE:
                conn.execute("DELETE FROM public.students WHERE id=%s AND organization_id=%s", (match.group(1), current_org_id(conn)))
            else:
                conn.execute("DELETE FROM students WHERE id=?", (int(match.group(1)),))
            conn.commit()
        self.send_json({"ok": True})


if __name__ == "__main__":
    if PG_MODE:
        ensure_pg_defaults()
    else:
        init_db(force="--reseed" in sys.argv)
        ensure_meta_defaults()
    port = int(os.environ.get("PORT", "8765"))
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    print(f"SMP tracking site running at http://0.0.0.0:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
