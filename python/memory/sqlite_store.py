from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


def _default_db_path() -> str:
    base = os.path.join(os.path.dirname(__file__), "..", "data")
    base = os.path.abspath(base)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "memory.sqlite")


@dataclass
class MemoryMessage:
    role: str
    content: str
    ts_ms: int


class SqliteMemoryStore:
    """
    Durable memory store for conversation history + preferences.

    Uses a single sqlite DB file on disk.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or os.getenv("ASSISTANT_DB_PATH") or _default_db_path()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_ts
                ON conversation_messages(ts_ms);
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_ts_ms INTEGER NOT NULL
                );
                """
            )
            self._conn.commit()

    def add_message(self, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        ts_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversation_messages (ts_ms, role, content) VALUES (?, ?, ?);",
                (ts_ms, role, content),
            )
            self._conn.commit()

    def get_recent_messages(self, limit: int = 20) -> List[Dict[str, str]]:
        limit = max(0, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, content
                FROM conversation_messages
                ORDER BY id DESC
                LIMIT ?;
                """,
                (limit,),
            ).fetchall()
        # reverse to chronological order
        rows = list(reversed(rows))
        return [{"role": str(r["role"]), "content": str(r["content"])} for r in rows]

    def set_preference(self, key: str, value: str) -> None:
        key = (key or "").strip()
        value = (value or "").strip()
        if not key:
            return
        ts_ms = int(time.time() * 1000)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO preferences (key, value, updated_ts_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_ts_ms=excluded.updated_ts_ms;
                """,
                (key, value, ts_ms),
            )
            self._conn.commit()

    def get_preferences(self) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM preferences ORDER BY key;").fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows}


