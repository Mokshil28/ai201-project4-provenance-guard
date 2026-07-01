"""SQLite persistence for Provenance Guard.

Two roles:
  - `submissions`  : the content store (looked up by content_id for appeals).
  - `audit_log`    : the structured, append-only record of every decision and,
                     from Milestone 5, every appeal. Nothing is overwritten.

The `appeals` table is created now so Milestone 5 needs no schema migration.
"""

import json
import os
import sqlite3

DB_PATH = os.environ.get("PROVENANCE_DB", "provenance.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                content_id  TEXT PRIMARY KEY,
                creator_id  TEXT,
                text        TEXT,
                attribution TEXT,
                confidence  REAL,
                p_ai        REAL,
                status      TEXT,
                created_at  TEXT,
                data        TEXT   -- full decision snapshot as JSON
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id  TEXT,
                entry_type  TEXT,   -- 'decision' | 'appeal'
                timestamp   TEXT,
                data        TEXT    -- entry payload as JSON
            );

            CREATE TABLE IF NOT EXISTS appeals (
                appeal_id   TEXT PRIMARY KEY,
                content_id  TEXT,
                reason      TEXT,
                created_at  TEXT
            );
            """
        )


def save_submission(record):
    """Persist a decision to the content store."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO submissions
                (content_id, creator_id, text, attribution, confidence, p_ai,
                 status, created_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["content_id"],
                record.get("creator_id"),
                record.get("text"),
                record.get("attribution"),
                record.get("confidence"),
                record.get("p_ai"),
                record.get("status"),
                record.get("timestamp"),
                json.dumps(record),
            ),
        )


def get_submission(content_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT data FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
    return json.loads(row["data"]) if row else None


def update_status(content_id, status):
    """Flip a submission's status (e.g. 'classified' -> 'under_review')."""
    with _connect() as conn:
        # keep the JSON snapshot's status field in sync too
        row = conn.execute(
            "SELECT data FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
        if row is None:
            return False
        data = json.loads(row["data"])
        data["status"] = status
        conn.execute(
            "UPDATE submissions SET status = ?, data = ? WHERE content_id = ?",
            (status, json.dumps(data), content_id),
        )
        return True


def save_appeal(appeal):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO appeals (appeal_id, content_id, reason, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                appeal["appeal_id"],
                appeal["content_id"],
                appeal.get("reason"),
                appeal.get("created_at"),
            ),
        )


def add_audit(entry_type, content_id, entry):
    """Append a structured entry to the audit log."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (content_id, entry_type, timestamp, data) "
            "VALUES (?, ?, ?, ?)",
            (content_id, entry_type, entry.get("timestamp"), json.dumps(entry)),
        )


def get_log(limit=50):
    """Return the most recent audit-log entries, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT entry_type, data FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    entries = []
    for row in rows:
        payload = json.loads(row["data"])
        payload["entry_type"] = row["entry_type"]
        entries.append(payload)
    return entries
