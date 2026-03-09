from __future__ import annotations

import sqlite3
from pathlib import Path


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fingerprints (
            fingerprint  TEXT PRIMARY KEY,
            imported_at  TEXT NOT NULL DEFAULT (datetime('now')),
            account_id   TEXT NOT NULL,
            description  TEXT NOT NULL
        )
    """)
    conn.commit()


def is_duplicate(conn: sqlite3.Connection, fingerprint: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fingerprints WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    return row is not None


def record(conn: sqlite3.Connection, fingerprint: str, account_id: str, description: str) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO fingerprints (fingerprint, account_id, description)
           VALUES (?, ?, ?)""",
        (fingerprint, account_id, description),
    )
    conn.commit()
