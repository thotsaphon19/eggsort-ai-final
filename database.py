"""
EggSort AI — Database layer
SQLite (Render Disk / local) — ไม่ต้องติดตั้งอะไรเพิ่ม

Schema:
  scans       — บันทึกผลการสแกนทุกฟอง
  sessions    — เซสชันสายพานแต่ละครั้ง
  daily_stats — สถิติรายวัน (materialised ทุกครั้งที่ query)
"""

import sqlite3, os, json
from datetime import datetime, date, timedelta
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(os.environ.get("DB_PATH", "logs/eggsort.db"))

# ─── Schema ───────────────────────────────────────────────────────────────────
SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    total       INTEGER DEFAULT 0,
    pass_count  INTEGER DEFAULT 0,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER REFERENCES sessions(id),
    scanned_at      TEXT    NOT NULL,
    grade           TEXT    NOT NULL CHECK(grade IN ('AA','A','B','C')),
    estimated_weight TEXT,
    confidence      INTEGER,
    shell_condition TEXT,
    color           TEXT,
    shape           TEXT,
    recommendation  TEXT,
    notes           TEXT,
    image_path      TEXT
);

CREATE INDEX IF NOT EXISTS idx_scans_date    ON scans(scanned_at);
CREATE INDEX IF NOT EXISTS idx_scans_grade   ON scans(grade);
CREATE INDEX IF NOT EXISTS idx_scans_session ON scans(session_id);
"""

# ─── Connection ───────────────────────────────────────────────────────────────
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(SCHEMA)
    return DB_PATH

@contextmanager
def get_con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

# ─── Write ────────────────────────────────────────────────────────────────────
def insert_scan(result: dict, session_id: int | None = None, image_path: str | None = None) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_con() as con:
        cur = con.execute("""
            INSERT INTO scans
              (session_id, scanned_at, grade, estimated_weight, confidence,
               shell_condition, color, shape, recommendation, notes, image_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            session_id,
            now,
            result.get("grade", "B"),
            result.get("estimatedWeight"),
            result.get("confidence"),
            result.get("shellCondition"),
            result.get("color"),
            result.get("shape"),
            result.get("recommendation"),
            result.get("notes"),
            image_path,
        ))
        scan_id = cur.lastrowid
        if session_id:
            passed = 1 if result.get("grade") in ("AA", "A") else 0
            con.execute("""
                UPDATE sessions
                SET total = total + 1, pass_count = pass_count + ?
                WHERE id = ?
            """, (passed, session_id))
    return scan_id

def start_session(notes: str = "") -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_con() as con:
        cur = con.execute(
            "INSERT INTO sessions (started_at, notes) VALUES (?,?)", (now, notes)
        )
        return cur.lastrowid

def end_session(session_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with get_con() as con:
        con.execute(
            "UPDATE sessions SET ended_at=? WHERE id=?", (now, session_id)
        )

# ─── Read — History ───────────────────────────────────────────────────────────
def get_scans(
    date_from: str | None = None,
    date_to:   str | None = None,
    grade:     str | None = None,
    limit:     int = 200,
    offset:    int = 0,
) -> list[dict]:
    """
    ดึงประวัติการสแกน กรองตาม date / grade
    date format: YYYY-MM-DD
    """
    clauses, params = [], []
    if date_from:
        clauses.append("scanned_at >= ?")
        params.append(date_from + "T00:00:00")
    if date_to:
        clauses.append("scanned_at <= ?")
        params.append(date_to + "T23:59:59")
    if grade:
        clauses.append("grade = ?")
        params.append(grade.upper())

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params += [limit, offset]

    with get_con() as con:
        rows = con.execute(f"""
            SELECT id, scanned_at, grade, estimated_weight, confidence,
                   shell_condition, color, recommendation, notes, session_id
            FROM   scans
            {where}
            ORDER  BY scanned_at DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()
    return [dict(r) for r in rows]

def count_scans(date_from=None, date_to=None, grade=None) -> int:
    clauses, params = [], []
    if date_from:
        clauses.append("scanned_at >= ?"); params.append(date_from + "T00:00:00")
    if date_to:
        clauses.append("scanned_at <= ?");   params.append(date_to + "T23:59:59")
    if grade:
        clauses.append("grade = ?");          params.append(grade.upper())
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_con() as con:
        return con.execute(f"SELECT COUNT(*) FROM scans {where}", params).fetchone()[0]

# ─── Read — Stats ─────────────────────────────────────────────────────────────
def daily_summary(days: int = 30) -> list[dict]:
    """สรุปรายวัน N วันย้อนหลัง"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    with get_con() as con:
        rows = con.execute("""
            SELECT
                substr(scanned_at,1,10)          AS day,
                COUNT(*)                          AS total,
                SUM(grade IN ('AA','A'))           AS pass_count,
                SUM(grade = 'AA')                 AS cnt_AA,
                SUM(grade = 'A')                  AS cnt_A,
                SUM(grade = 'B')                  AS cnt_B,
                SUM(grade = 'C')                  AS cnt_C,
                ROUND(AVG(confidence),1)           AS avg_conf,
                ROUND(100.0*SUM(grade IN ('AA','A'))/COUNT(*),1) AS pass_rate
            FROM   scans
            WHERE  scanned_at >= ?
            GROUP  BY day
            ORDER  BY day ASC
        """, (since,)).fetchall()
    return [dict(r) for r in rows]

def grade_distribution(date_from=None, date_to=None) -> dict:
    """สัดส่วนเกรดในช่วงเวลา"""
    clauses, params = [], []
    if date_from:
        clauses.append("scanned_at >= ?"); params.append(date_from + "T00:00:00")
    if date_to:
        clauses.append("scanned_at <= ?");   params.append(date_to + "T23:59:59")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_con() as con:
        row = con.execute(f"""
            SELECT
                COUNT(*)                  AS total,
                SUM(grade='AA')           AS AA,
                SUM(grade='A')            AS A,
                SUM(grade='B')            AS B,
                SUM(grade='C')            AS C,
                ROUND(AVG(confidence),1)  AS avg_conf,
                ROUND(100.0*SUM(grade IN ('AA','A'))/MAX(COUNT(*),1),1) AS pass_rate
            FROM scans {where}
        """, params).fetchone()
    return dict(row) if row else {}

def hourly_summary(day: str | None = None) -> list[dict]:
    """สรุปรายชั่วโมง (default = วันนี้)"""
    day = day or date.today().strftime("%Y-%m-%d")
    with get_con() as con:
        rows = con.execute("""
            SELECT
                substr(scanned_at,12,2)   AS hour,
                COUNT(*)                  AS total,
                SUM(grade IN ('AA','A'))   AS pass_count,
                SUM(grade='AA') AS AA, SUM(grade='A') AS A,
                SUM(grade='B')  AS B,  SUM(grade='C') AS C
            FROM   scans
            WHERE  scanned_at LIKE ?
            GROUP  BY hour
            ORDER  BY hour
        """, (day + "%",)).fetchall()
    return [dict(r) for r in rows]

def get_sessions(limit: int = 50) -> list[dict]:
    with get_con() as con:
        rows = con.execute("""
            SELECT id, started_at, ended_at, total, pass_count,
                   ROUND(100.0*pass_count/MAX(total,1),1) AS pass_rate, notes
            FROM   sessions
            ORDER  BY started_at DESC
            LIMIT  ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def shell_condition_summary(date_from=None, date_to=None) -> list[dict]:
    clauses, params = [], []
    if date_from:
        clauses.append("scanned_at >= ?"); params.append(date_from + "T00:00:00")
    if date_to:
        clauses.append("scanned_at <= ?");   params.append(date_to + "T23:59:59")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_con() as con:
        rows = con.execute(f"""
            SELECT shell_condition, COUNT(*) AS cnt
            FROM   scans {where}
            GROUP  BY shell_condition
            ORDER  BY cnt DESC
        """, params).fetchall()
    return [dict(r) for r in rows]
