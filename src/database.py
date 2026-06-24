"""
SQLite database layer.
Tables: admins, tasks, uploads, notes
"""

import sqlite3
import os
from config import DB_PATH
from datetime import datetime

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            full_name TEXT,
            added_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id    INTEGER NOT NULL,
            title       TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority    TEXT DEFAULT 'normal',
            deadline    TEXT DEFAULT '',
            status      TEXT DEFAULT 'pending',
            created_at  TEXT DEFAULT (datetime('now')),
            done_at     TEXT DEFAULT '',
            verified_at TEXT DEFAULT '',
            reject_reason TEXT DEFAULT '',
            FOREIGN KEY (admin_id) REFERENCES admins(user_id)
        );

        CREATE TABLE IF NOT EXISTS uploads (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            url        TEXT UNIQUE NOT NULL,
            quality    TEXT DEFAULT '',
            size       TEXT DEFAULT '',
            seen_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS task_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    INTEGER NOT NULL,
            author_id  INTEGER NOT NULL,
            note       TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        """)
    print("✅ Database initialised.")


# ─── ADMIN CRUD ─────────────────────────────────────────────────

def add_admin(user_id: int, username: str, full_name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, username, full_name) VALUES (?,?,?)",
            (user_id, username, full_name)
        )

def remove_admin(user_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))

def get_all_admins():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins ORDER BY added_at DESC").fetchall()

def is_admin(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None

def get_admin(user_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM admins WHERE user_id = ?", (user_id,)).fetchone()

def update_admin_name(user_id: int, username: str, full_name: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE admins SET username=?, full_name=? WHERE user_id=?",
            (username, full_name, user_id)
        )


# ─── TASK CRUD ──────────────────────────────────────────────────

def create_task(admin_id: int, title: str, description: str = "",
                priority: str = "normal", deadline: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (admin_id, title, description, priority, deadline) VALUES (?,?,?,?,?)",
            (admin_id, title, description, priority, deadline)
        )
        return cur.lastrowid

def get_task(task_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

def get_admin_tasks(admin_id: int, status: str = None):
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM tasks WHERE admin_id = ? AND status = ? ORDER BY created_at DESC",
                (admin_id, status)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM tasks WHERE admin_id = ? ORDER BY created_at DESC",
            (admin_id,)
        ).fetchall()

def get_all_tasks(status: str = None):
    with get_conn() as conn:
        if status:
            return conn.execute(
                """SELECT t.*, a.username, a.full_name
                   FROM tasks t JOIN admins a ON t.admin_id = a.user_id
                   WHERE t.status = ? ORDER BY t.created_at DESC""",
                (status,)
            ).fetchall()
        return conn.execute(
            """SELECT t.*, a.username, a.full_name
               FROM tasks t JOIN admins a ON t.admin_id = a.user_id
               ORDER BY t.created_at DESC"""
        ).fetchall()

def update_task_status(task_id: int, status: str, reject_reason: str = ""):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        if status == "done":
            conn.execute(
                "UPDATE tasks SET status=?, done_at=? WHERE id=?",
                (status, now, task_id)
            )
        elif status in ("verified", "rejected"):
            conn.execute(
                "UPDATE tasks SET status=?, verified_at=?, reject_reason=? WHERE id=?",
                (status, now, reject_reason, task_id)
            )
        else:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))

def get_task_stats():
    """Returns dict of status -> count."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

def get_admin_stats(admin_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM tasks WHERE admin_id=? GROUP BY status",
            (admin_id,)
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}


# ─── TASK NOTES ─────────────────────────────────────────────────

def add_note(task_id: int, author_id: int, note: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO task_notes (task_id, author_id, note) VALUES (?,?,?)",
            (task_id, author_id, note)
        )

def get_notes(task_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM task_notes WHERE task_id=? ORDER BY created_at ASC",
            (task_id,)
        ).fetchall()


# ─── UPLOADS ────────────────────────────────────────────────────

def upload_exists(url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM uploads WHERE url = ?", (url,)).fetchone()
        return row is not None

def save_upload(title: str, url: str, quality: str = "", size: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO uploads (title, url, quality, size) VALUES (?,?,?,?)",
            (title, url, quality, size)
        )

def get_recent_uploads(limit: int = 10):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM uploads ORDER BY seen_at DESC LIMIT ?", (limit,)
        ).fetchall()

def get_upload_count() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM uploads").fetchone()
        return row["cnt"] if row else 0


init_db()
