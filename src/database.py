"""SQLite database for persistent storage of notes, agenda, and settings.

Thread-safe, auto-creates tables on first use.
Database file: /app/data/claudephone.db (mounted from host via docker-compose).
"""

import logging
import sqlite3
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = "/app/data/claudephone.db"


class Database:
    """Thread-safe SQLite database for ClaudePhone."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()
        self._ensure_dir()
        self._init_tables()

    def _ensure_dir(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_tables(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                completed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS agenda (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
        """)
        conn.commit()
        logger.info("Database initialized: %s", self.db_path)

    # --- Notes ---

    def add_note(self, content: str) -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO notes (content) VALUES (?)", (content,)
        )
        conn.commit()
        return cur.lastrowid

    def get_notes(self, include_completed: bool = False, limit: int = 20) -> List[Dict]:
        conn = self._get_conn()
        if include_completed:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notes WHERE completed = 0 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def complete_note(self, note_id: int) -> bool:
        conn = self._get_conn()
        cur = conn.execute("UPDATE notes SET completed = 1 WHERE id = ?", (note_id,))
        conn.commit()
        return cur.rowcount > 0

    def delete_note(self, note_id: int) -> bool:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return cur.rowcount > 0

    def clear_completed_notes(self) -> int:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM notes WHERE completed = 1")
        conn.commit()
        return cur.rowcount

    # --- Agenda ---

    def add_event(self, title: str, event_date: str, event_time: Optional[str] = None,
                  description: Optional[str] = None) -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO agenda (title, event_date, event_time, description) VALUES (?, ?, ?, ?)",
            (title, event_date, event_time, description),
        )
        conn.commit()
        return cur.lastrowid

    def get_events(self, event_date: Optional[str] = None, limit: int = 10) -> List[Dict]:
        conn = self._get_conn()
        if event_date:
            rows = conn.execute(
                "SELECT * FROM agenda WHERE event_date = ? ORDER BY event_time ASC LIMIT ?",
                (event_date, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agenda WHERE event_date >= ? ORDER BY event_date ASC, event_time ASC LIMIT ?",
                (date.today().isoformat(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_events_today(self) -> List[Dict]:
        return self.get_events(date.today().isoformat())

    def get_events_tomorrow(self) -> List[Dict]:
        from datetime import timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        return self.get_events(tomorrow)

    def delete_event(self, event_id: int) -> bool:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM agenda WHERE id = ?", (event_id,))
        conn.commit()
        return cur.rowcount > 0

    def delete_event_by_title(self, title: str) -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "DELETE FROM agenda WHERE LOWER(title) LIKE ?",
            (f"%{title.lower()}%",),
        )
        conn.commit()
        return cur.rowcount

    # --- Settings ---

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now', 'localtime'))",
            (key, value),
        )
        conn.commit()

    def get_all_settings(self) -> Dict[str, str]:
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def delete_setting(self, key: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()
        return cur.rowcount > 0

    def is_setup_complete(self) -> bool:
        return self.get_setting("setup_complete", "false") == "true"

    def mark_setup_complete(self) -> None:
        self.set_setting("setup_complete", "true")
