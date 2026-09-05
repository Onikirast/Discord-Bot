"""
database.py

Async SQLite data layer for the bot's reminders and timetable features.
Uses aiosqlite so DB calls never block the event loop.

Schema design notes:
- All timestamps are stored as ISO-8601 strings in UTC (timezone-aware,
  e.g. '2026-08-01T14:30:00+00:00'). Conversion to a user's local time
  happens only at display time.
- `reminders.recurrence` is one of: 'once', 'daily', 'weekly'. Using a
  single table + column keeps the query surface simple (one "find due
  items" query) instead of separate one-off/recurring tables.
- `timetable_notifications` exists purely to prevent duplicate pings if
  the bot restarts mid-day and re-scans today's entries.

Connection handling: a single shared aiosqlite connection is opened once
(in init_db) and reused for every query, rather than opening and closing
a fresh connection per call. aiosqlite serializes access through its own
background thread, so sharing one connection across concurrent callers
is safe -- and avoids the real overhead of a fresh connect/close cycle
on every single database operation, which matters here since the
scheduler loop calls into this module every 30 seconds indefinitely.
"""

import aiosqlite
from datetime import datetime, timedelta, timezone

DB_PATH = "bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    remind_at TEXT NOT NULL,          -- ISO datetime (UTC)
    recurrence TEXT NOT NULL DEFAULT 'once',  -- once | daily | weekly
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | cancelled
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, remind_at);
CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders (user_id);

CREATE TABLE IF NOT EXISTS timetable_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,     -- 0=Monday ... 6=Sunday
    start_time TEXT NOT NULL,         -- 'HH:MM' 24h, UTC
    title TEXT NOT NULL,
    notify_before_minutes INTEGER NOT NULL DEFAULT 10,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timetable_user ON timetable_entries (user_id);
CREATE INDEX IF NOT EXISTS idx_timetable_day ON timetable_entries (day_of_week);

CREATE TABLE IF NOT EXISTS timetable_notifications (
    entry_id INTEGER NOT NULL,
    notified_date TEXT NOT NULL,      -- 'YYYY-MM-DD'
    PRIMARY KEY (entry_id, notified_date)
);

CREATE TABLE IF NOT EXISTS dcu_links (
    user_id INTEGER PRIMARY KEY,
    course_name TEXT NOT NULL,        -- e.g. 'COMSCI1'
    course_description TEXT,          -- e.g. 'BSc in Computer Science'
    course_identity TEXT NOT NULL,    -- UUID from the TimetableSync API
    linked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    module_name TEXT NOT NULL,   -- e.g. 'CSC1018[2] Logic', self-selected via
                                  -- the same live DCU module search used elsewhere
    grade REAL NOT NULL,         -- percentage mark, 0-100 (self-reported, not
                                  -- pulled from any DCU record)
    credits REAL NOT NULL DEFAULT 5,  -- ECTS credits, for weighting the average
    semester TEXT,                -- free text, e.g. 'Year 2 Semester 2'
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grades_user ON grades (user_id);
"""


def _now_iso() -> str:
    """Timezone-aware UTC timestamp, e.g. '2026-08-01T14:30:00+00:00'.

    Replaces the old datetime.utcnow() (deprecated by Python, scheduled
    for removal). Kept as one helper so every INSERT uses the exact same
    format consistently -- important because these values get compared
    as plain TEXT in SQL (e.g. "remind_at <= ?"), so any inconsistency
    in format between rows would silently produce wrong comparisons
    rather than an error.
    """
    return datetime.now(timezone.utc).isoformat()


_connection: aiosqlite.Connection | None = None


async def init_db():
    """Open (or re-open) the shared connection and ensure the schema exists.

    Safe to call more than once -- e.g. tests call this per-test (via the
    temp_db fixture) after pointing DB_PATH at a fresh temporary file,
    which correctly closes any previous connection and opens a new one
    against the new path, keeping tests isolated from each other.
    """
    global _connection
    if _connection is not None:
        await _connection.close()
    _connection = await aiosqlite.connect(DB_PATH)
    _connection.row_factory = aiosqlite.Row
    await _connection.executescript(SCHEMA)
    await _connection.commit()


async def close_db():
    """Close the shared connection. Call this on bot shutdown."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None


def _db() -> aiosqlite.Connection:
    if _connection is None:
        raise RuntimeError("Database not initialized -- call init_db() first.")
    return _connection


# ---------- Reminders ----------

async def add_reminder(user_id: int, channel_id: int, message: str,
                        remind_at: datetime, recurrence: str = "once") -> int:
    db = _db()
    cursor = await db.execute(
        "INSERT INTO reminders (user_id, channel_id, message, remind_at, "
        "recurrence, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (user_id, channel_id, message, remind_at.isoformat(), recurrence, _now_iso()),
    )
    await db.commit()
    return cursor.lastrowid


async def get_due_reminders(now: datetime):
    db = _db()
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE status = 'pending' AND remind_at <= ?",
        (now.isoformat(),),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def reschedule_or_close_reminder(reminder_id: int, recurrence: str,
                                        old_remind_at: datetime):
    db = _db()
    if recurrence == "daily":
        next_time = old_remind_at + timedelta(days=1)
        await db.execute("UPDATE reminders SET remind_at = ? WHERE id = ?",
                          (next_time.isoformat(), reminder_id))
    elif recurrence == "weekly":
        next_time = old_remind_at + timedelta(weeks=1)
        await db.execute("UPDATE reminders SET remind_at = ? WHERE id = ?",
                          (next_time.isoformat(), reminder_id))
    else:  # 'once'
        await db.execute("UPDATE reminders SET status = 'sent' WHERE id = ?",
                          (reminder_id,))
    await db.commit()


async def list_reminders(user_id: int):
    db = _db()
    cursor = await db.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND status = 'pending' "
        "ORDER BY remind_at ASC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_reminder(user_id: int, reminder_id: int) -> bool:
    db = _db()
    cursor = await db.execute(
        "DELETE FROM reminders WHERE id = ? AND user_id = ?",
        (reminder_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


# ---------- Timetable ----------

async def add_timetable_entry(user_id: int, channel_id: int, day_of_week: int,
                               start_time: str, title: str,
                               notify_before_minutes: int = 10) -> int:
    db = _db()
    cursor = await db.execute(
        "INSERT INTO timetable_entries (user_id, channel_id, day_of_week, "
        "start_time, title, notify_before_minutes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, channel_id, day_of_week, start_time, title,
         notify_before_minutes, _now_iso()),
    )
    await db.commit()
    return cursor.lastrowid


async def list_timetable(user_id: int):
    db = _db()
    cursor = await db.execute(
        "SELECT * FROM timetable_entries WHERE user_id = ? "
        "ORDER BY day_of_week ASC, start_time ASC",
        (user_id,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_entries_for_day(day_of_week: int):
    db = _db()
    cursor = await db.execute(
        "SELECT * FROM timetable_entries WHERE day_of_week = ?",
        (day_of_week,),
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_timetable_entry(user_id: int, entry_id: int) -> bool:
    db = _db()
    cursor = await db.execute(
        "DELETE FROM timetable_entries WHERE id = ? AND user_id = ?",
        (entry_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def was_notified(entry_id: int, date_str: str) -> bool:
    db = _db()
    cursor = await db.execute(
        "SELECT 1 FROM timetable_notifications WHERE entry_id = ? AND notified_date = ?",
        (entry_id, date_str),
    )
    return await cursor.fetchone() is not None


async def mark_notified(entry_id: int, date_str: str):
    db = _db()
    await db.execute(
        "INSERT OR IGNORE INTO timetable_notifications (entry_id, notified_date) "
        "VALUES (?, ?)",
        (entry_id, date_str),
    )
    await db.commit()


# ---------- DCU course link ----------

async def set_dcu_link(user_id: int, course_name: str, course_description: str | None,
                        course_identity: str):
    db = _db()
    await db.execute(
        "INSERT INTO dcu_links (user_id, course_name, course_description, "
        "course_identity, linked_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET course_name=excluded.course_name, "
        "course_description=excluded.course_description, "
        "course_identity=excluded.course_identity, linked_at=excluded.linked_at",
        (user_id, course_name, course_description, course_identity, _now_iso()),
    )
    await db.commit()


async def get_dcu_link(user_id: int):
    db = _db()
    cursor = await db.execute(
        "SELECT * FROM dcu_links WHERE user_id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def delete_dcu_link(user_id: int) -> bool:
    db = _db()
    cursor = await db.execute("DELETE FROM dcu_links WHERE user_id = ?", (user_id,))
    await db.commit()
    return cursor.rowcount > 0


# ---------- Grades ----------

async def add_grade(user_id: int, module_name: str, grade: float,
                     credits: float = 5.0, semester: str | None = None) -> int:
    db = _db()
    cursor = await db.execute(
        "INSERT INTO grades (user_id, module_name, grade, credits, semester, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, module_name, grade, credits, semester, _now_iso()),
    )
    await db.commit()
    return cursor.lastrowid


async def list_grades(user_id: int, semester: str | None = None):
    db = _db()
    if semester:
        cursor = await db.execute(
            "SELECT * FROM grades WHERE user_id = ? AND semester = ? ORDER BY created_at ASC",
            (user_id, semester),
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM grades WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_grade(user_id: int, grade_id: int) -> bool:
    db = _db()
    cursor = await db.execute(
        "DELETE FROM grades WHERE id = ? AND user_id = ?", (grade_id, user_id)
    )
    await db.commit()
    return cursor.rowcount > 0
