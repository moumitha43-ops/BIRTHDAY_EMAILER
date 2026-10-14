"""
helpers.py — BIT Birthday Wishes
SQLite DB, single-admin auth, config, CSV parsing, logging
"""
import os, re, sqlite3, hashlib, logging, csv
from pathlib import Path
from datetime import datetime, date, timezone, timedelta
from logging.handlers import RotatingFileHandler

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DATA_DIR   = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH    = DATA_DIR / "app.db"
LOG_PATH   = DATA_DIR / "app.log"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
TPL_PATH   = DATA_DIR / "template.html"

for _d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Logger ────────────────────────────────────────────────────────────────────
def _make_logger():
    log = logging.getLogger("bit_bday")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    fh = RotatingFileHandler(LOG_PATH, maxBytes=500_000, backupCount=3,
                             encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return log

logger = _make_logger()

# ── DB ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS students (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            dob        TEXT NOT NULL,
            email      TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sent_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            name       TEXT,
            email      TEXT,
            sent_on    TEXT,
            status     TEXT,
            error      TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)
    logger.info("DB initialised at %s", DB_PATH)

# ── Config ────────────────────────────────────────────────────────────────────
def cfg_get(key: str, default: str = "") -> str:
    with get_db() as db:
        row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def cfg_set(key: str, value):
    with get_db() as db:
        db.execute(
            "INSERT INTO config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value) if value is not None else ""))

def cfg_set_many(d: dict):
    with get_db() as db:
        for k, v in d.items():
            db.execute(
                "INSERT INTO config(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v) if v is not None else ""))

def get_all_config() -> dict:
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM config").fetchall()
    return {r["key"]: r["value"] for r in rows}

# ── Local time (follows the timezone chosen in Scheduler) ─────────────────────
def _tz():
    """Configured timezone; falls back to IST if tz data is unavailable."""
    name = cfg_get("timezone", "Asia/Kolkata") or "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=5, minutes=30))

def now_local() -> datetime:
    return datetime.now(_tz())

def today_local() -> date:
    return now_local().date()

def utc_to_local_str(utc_text: str, fmt: str = "%d %b %Y, %H:%M:%S") -> str:
    """SQLite datetime('now') is UTC 'YYYY-MM-DD HH:MM:SS' -> local string."""
    try:
        dt = datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(_tz()).strftime(fmt)
    except Exception:
        return utc_text or ""

# ── College logo (uploaded from Settings, or dropped in static/logo.png) ──────
LOGO_EXTS = ("png", "jpg", "jpeg")

def find_logo():
    """Return Path of the logo file, or None. Uploaded logo wins over static/."""
    for folder in (DATA_DIR, BASE_DIR / "static"):
        for ext in LOGO_EXTS:
            p = folder / f"logo.{ext}"
            if p.exists():
                return p
    return None

def save_logo(data: bytes, ext: str):
    remove_logo()
    (DATA_DIR / f"logo.{ext}").write_bytes(data)
    logger.info("College logo uploaded (%d bytes)", len(data))

def remove_logo():
    for ext in LOGO_EXTS:
        p = DATA_DIR / f"logo.{ext}"
        if p.exists():
            p.unlink()

# ── Auth ──────────────────────────────────────────────────────────────────────
def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

# ── Hardcoded credentials ─────────────────────────────────────────────────────
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

def is_setup_done() -> bool:
    """Always True — credentials are hardcoded, no setup step needed."""
    return True

def create_admin(username: str, password: str):
    """No-op — credentials are hardcoded."""
    pass

def check_login(username: str, password: str) -> bool:
    return (username.strip() == ADMIN_USERNAME and
            password == ADMIN_PASSWORD)

def get_admin_username() -> str:
    return ADMIN_USERNAME

# ── Student helpers ───────────────────────────────────────────────────────────
def parse_dob(raw: str):
    """Parse DD-MM-YYYY, DD/MM/YYYY, or YYYY-MM-DD → date or None."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    parts = re.findall(r"\d+", raw)
    if len(parts) >= 3:
        a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
        y = c if c > 31 else a
        m = b
        d = a if c > 31 else b if a > 12 else a
        if y < 100:
            y += 2000
        try:
            return date(y, m, d)
        except ValueError:
            pass
    return None

def validate_email_fmt(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$", email.strip()))

def import_csv_file(filepath: str) -> dict:
    """Parse CSV, replace all students, return {valid, errors}."""
    valid, errors = [], []
    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        missing = {"name", "dob", "email"} - set(headers)
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")

        for i, row in enumerate(reader, 1):
            name  = (row.get("name")  or "").strip()
            dob_r = (row.get("dob")   or row.get("date_of_birth") or "").strip()
            email = (row.get("email") or "").strip()
            issues = []
            if not name:                    issues.append("missing name")
            if not email:                   issues.append("missing email")
            elif not validate_email_fmt(email): issues.append("invalid email")
            dob = parse_dob(dob_r)
            if not dob_r:                   issues.append("missing dob")
            elif dob is None:               issues.append(f"bad dob '{dob_r}'")
            if issues:
                errors.append({"row": i, "name": name,
                               "email": email, "reason": "; ".join(issues)})
            else:
                valid.append({"name": name,
                              "dob":  dob.strftime("%d-%m-%Y"),
                              "email": email})

    with get_db() as db:
        db.execute("DELETE FROM students")
        db.executemany(
            "INSERT INTO students(name,dob,email) VALUES(:name,:dob,:email)",
            valid)
    logger.info("CSV import: %d valid, %d errors", len(valid), len(errors))
    return {"valid": valid, "errors": errors}

def get_all_students() -> list:
    with get_db() as db:
        return [dict(r) for r in
                db.execute("SELECT * FROM students ORDER BY name").fetchall()]

def delete_student(sid: int):
    with get_db() as db:
        db.execute("DELETE FROM students WHERE id=?", (sid,))

def get_todays_birthdays() -> list:
    today = today_local()
    students = get_all_students()
    return [s for s in students
            if (d := parse_dob(s["dob"])) and
               d.month == today.month and d.day == today.day]

def get_birthdays_on(day: int, month: int) -> list:
    students = get_all_students()
    return [s for s in students
            if (d := parse_dob(s["dob"])) and
               d.month == month and d.day == day]

def get_month_birthday_map(month: int) -> dict:
    """Return {day_str: [student, ...]} for the given month."""
    students = get_all_students()
    result = {}
    for s in students:
        d = parse_dob(s["dob"])
        if d and d.month == month:
            result.setdefault(str(d.day), []).append(s)
    return result

# ── Sent log ──────────────────────────────────────────────────────────────────
def already_sent_today(student_id: int) -> bool:
    today = today_local().isoformat()
    with get_db() as db:
        row = db.execute(
            "SELECT id FROM sent_log WHERE student_id=? AND sent_on=? AND status='sent'",
            (student_id, today)).fetchone()
    return row is not None

def log_sent(student_id: int, name: str, email: str,
             status: str, error: str = ""):
    with get_db() as db:
        db.execute(
            "INSERT INTO sent_log(student_id,name,email,sent_on,status,error)"
            " VALUES(?,?,?,?,?,?)",
            (student_id, name, email, today_local().isoformat(), status, error))

def get_recent_logs(n: int = 100) -> list:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM sent_log ORDER BY id DESC LIMIT ?",
            (n,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["time"] = utc_to_local_str(d.get("created_at", ""))
        out.append(d)
    return out

# ── Template ──────────────────────────────────────────────────────────────────
# Fallback: project-root template.html (committed to repo, used on fresh deploy)
_ROOT_TPL = BASE_DIR / "template.html"

def get_template() -> str:
    if TPL_PATH.exists():
        return TPL_PATH.read_text(encoding="utf-8")
    if _ROOT_TPL.exists():
        return _ROOT_TPL.read_text(encoding="utf-8")
    return ""

def save_template(html: str):
    TPL_PATH.write_text(html, encoding="utf-8")
    logger.info("Template saved (%d bytes)", len(html))
