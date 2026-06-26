"""SQLite database layer — admins, tasks, uploads, notes, watchlist."""

import sqlite3, os
from datetime import datetime
from config import DB_PATH

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
            username  TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            added_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id      INTEGER NOT NULL,
            title         TEXT NOT NULL,
            description   TEXT DEFAULT '',
            priority      TEXT DEFAULT 'normal',
            deadline      TEXT DEFAULT '',
            status        TEXT DEFAULT 'pending',
            created_at    TEXT DEFAULT (datetime('now')),
            done_at       TEXT DEFAULT '',
            verified_at   TEXT DEFAULT '',
            reject_reason TEXT DEFAULT '',
            FOREIGN KEY (admin_id) REFERENCES admins(user_id)
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            title    TEXT NOT NULL,
            url      TEXT UNIQUE NOT NULL,
            quality  TEXT DEFAULT '',
            size     TEXT DEFAULT '',
            seen_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS task_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    INTEGER NOT NULL,
            author_id  INTEGER NOT NULL,
            note       TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            title    TEXT NOT NULL,
            url      TEXT NOT NULL,
            quality  TEXT DEFAULT '',
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, url)
        );
        CREATE TABLE IF NOT EXISTS bot_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
    print("✅ Database initialised.")

# ── Admin ───────────────────────────────────────────────────────
def add_admin(user_id, username, full_name):
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO admins (user_id,username,full_name) VALUES (?,?,?)",
                  (user_id, username, full_name))

def remove_admin(user_id):
    with get_conn() as c: c.execute("DELETE FROM admins WHERE user_id=?", (user_id,))

def get_all_admins():
    with get_conn() as c: return c.execute("SELECT * FROM admins ORDER BY added_at DESC").fetchall()

def is_admin(user_id):
    with get_conn() as c: return bool(c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone())

def get_admin(user_id):
    with get_conn() as c: return c.execute("SELECT * FROM admins WHERE user_id=?", (user_id,)).fetchone()

def update_admin_name(user_id, username, full_name):
    with get_conn() as c:
        c.execute("UPDATE admins SET username=?,full_name=? WHERE user_id=?", (username,full_name,user_id))

# ── Tasks ───────────────────────────────────────────────────────
def create_task(admin_id, title, description="", priority="normal", deadline=""):
    with get_conn() as c:
        cur = c.execute("INSERT INTO tasks (admin_id,title,description,priority,deadline) VALUES (?,?,?,?,?)",
                        (admin_id,title,description,priority,deadline))
        return cur.lastrowid

def get_task(task_id):
    with get_conn() as c: return c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

def get_admin_tasks(admin_id, status=None):
    with get_conn() as c:
        if status:
            return c.execute("SELECT * FROM tasks WHERE admin_id=? AND status=? ORDER BY created_at DESC",
                             (admin_id,status)).fetchall()
        return c.execute("SELECT * FROM tasks WHERE admin_id=? ORDER BY created_at DESC", (admin_id,)).fetchall()

def get_all_tasks(status=None):
    with get_conn() as c:
        if status:
            return c.execute(
                "SELECT t.*,a.username,a.full_name FROM tasks t JOIN admins a ON t.admin_id=a.user_id WHERE t.status=? ORDER BY t.created_at DESC",
                (status,)).fetchall()
        return c.execute(
            "SELECT t.*,a.username,a.full_name FROM tasks t JOIN admins a ON t.admin_id=a.user_id ORDER BY t.created_at DESC"
        ).fetchall()

def update_task_status(task_id, status, reject_reason=""):
    now = datetime.now().isoformat()
    with get_conn() as c:
        if status == "done":
            c.execute("UPDATE tasks SET status=?,done_at=? WHERE id=?", (status,now,task_id))
        elif status in ("verified","rejected"):
            c.execute("UPDATE tasks SET status=?,verified_at=?,reject_reason=? WHERE id=?",
                      (status,now,reject_reason,task_id))
        else:
            c.execute("UPDATE tasks SET status=? WHERE id=?", (status,task_id))

def get_task_stats():
    with get_conn() as c:
        return {r["status"]:r["cnt"] for r in
                c.execute("SELECT status,COUNT(*) as cnt FROM tasks GROUP BY status").fetchall()}

def get_admin_stats(admin_id):
    with get_conn() as c:
        return {r["status"]:r["cnt"] for r in
                c.execute("SELECT status,COUNT(*) as cnt FROM tasks WHERE admin_id=? GROUP BY status",
                          (admin_id,)).fetchall()}

# ── Notes ───────────────────────────────────────────────────────
def add_note(task_id, author_id, note):
    with get_conn() as c:
        c.execute("INSERT INTO task_notes (task_id,author_id,note) VALUES (?,?,?)", (task_id,author_id,note))

def get_notes(task_id):
    with get_conn() as c:
        return c.execute("SELECT * FROM task_notes WHERE task_id=? ORDER BY created_at ASC", (task_id,)).fetchall()

# ── Uploads ─────────────────────────────────────────────────────
def upload_exists(url):
    with get_conn() as c: return bool(c.execute("SELECT 1 FROM uploads WHERE url=?", (url,)).fetchone())

def save_upload(title, url, quality="", size=""):
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO uploads (title,url,quality,size) VALUES (?,?,?,?)",
                  (title,url,quality,size))

def get_recent_uploads(limit=80):
    with get_conn() as c:
        return c.execute("SELECT * FROM uploads ORDER BY seen_at DESC LIMIT ?", (limit,)).fetchall()

def get_upload_count():
    with get_conn() as c:
        r = c.execute("SELECT COUNT(*) as cnt FROM uploads").fetchone()
        return r["cnt"] if r else 0

# ── Watchlist ───────────────────────────────────────────────────
def watchlist_add(user_id, title, url, quality=""):
    with get_conn() as c:
        try:
            c.execute("INSERT INTO watchlist (user_id,title,url,quality) VALUES (?,?,?,?)",
                      (user_id,title,url,quality))
            return True
        except sqlite3.IntegrityError:
            return False  # already exists

def watchlist_remove(user_id, url):
    with get_conn() as c:
        c.execute("DELETE FROM watchlist WHERE user_id=? AND url=?", (user_id,url))

def watchlist_get(user_id):
    with get_conn() as c:
        return c.execute("SELECT * FROM watchlist WHERE user_id=? ORDER BY added_at DESC", (user_id,)).fetchall()

def watchlist_clear(user_id):
    with get_conn() as c:
        c.execute("DELETE FROM watchlist WHERE user_id=?", (user_id,))

# ── Bot Config ──────────────────────────────────────────────────
def config_get(key, default=None):
    with get_conn() as c:
        r = c.execute("SELECT value FROM bot_config WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def config_set(key, value):
    with get_conn() as c:
        c.execute("INSERT OR REPLACE INTO bot_config (key,value) VALUES (?,?)", (key,str(value)))

init_db()
