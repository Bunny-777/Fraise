import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import sqlite_vec

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "fraise.db"

_MIGRATIONS = [
    """
    CREATE VIRTUAL TABLE memories USING fts5(
        content,
        session_id UNINDEXED,
        created_at UNINDEXED
    );
    """,
    """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        session_id TEXT,
        filename TEXT,
        uploaded_at TEXT,
        char_count INTEGER,
        text TEXT
    );
    CREATE VIRTUAL TABLE chunks USING fts5(
        text,
        session_id UNINDEXED,
        document_id UNINDEXED,
        ordinal UNINDEXED
    );
    CREATE VIRTUAL TABLE vec_chunks USING vec0(
        chunk_id INTEGER PRIMARY KEY,
        session_id TEXT,
        embedding float[512]
    );
    """,
    """
    CREATE TABLE conversation_turns (
        id INTEGER PRIMARY KEY,
        session_id TEXT,
        role TEXT,
        content TEXT,
        created_at TEXT
    );
    """,
    """
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        title TEXT,
        format TEXT,
        body TEXT,
        created_at TEXT
    );
    CREATE INDEX idx_artifacts_session ON artifacts (session_id, created_at DESC);
    """,
    """
    CREATE TABLE days (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        text TEXT,
        status TEXT,
        spoken TEXT,
        error TEXT,
        tasks TEXT,
        created_at TEXT
    );
    CREATE INDEX idx_days_session ON days (session_id, created_at DESC);
    """,
    """
    CREATE TABLE dictations (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        raw_text TEXT,
        polished_text TEXT,
        is_email INTEGER DEFAULT 0,
        email_to TEXT,
        email_subject TEXT,
        email_body TEXT,
        status TEXT,
        sent_to TEXT,
        created_at TEXT
    );
    CREATE INDEX idx_dictations_session ON dictations (session_id, created_at DESC);
    """,
    """
    ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'document';
    """,
]

def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _migrate(conn)
    return conn

def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i in range(version, len(_MIGRATIONS)):
        conn.executescript(_MIGRATIONS[i])
        conn.execute(f"PRAGMA user_version = {i + 1}")
        conn.commit()

def new_id() -> str:
    return uuid4().hex

def save_day(
    day_id: str,
    session_id: str,
    text: str,
    status: str,
    tasks: list[dict],
    spoken: str | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO days (id, session_id, text, status, spoken, error, tasks, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, spoken = excluded.spoken, "
            "error = excluded.error, tasks = excluded.tasks",
            (day_id, session_id, text, status, spoken, error, json.dumps(tasks), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_day(day_id: str, session_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, text, status, spoken, error, tasks, created_at FROM days "
            "WHERE id = ? AND session_id = ?",
            (day_id, session_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    try:
        tasks = json.loads(row["tasks"] or "[]")
    except json.JSONDecodeError:
        tasks = []
    return {
        "id": row["id"],
        "text": row["text"],
        "status": row["status"],
        "spoken": row["spoken"],
        "error": row["error"],
        "tasks": tasks,
        "created_at": row["created_at"],
    }


def list_days(session_id: str, limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, text, status, tasks, created_at FROM days WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    days = []
    for row in rows:
        try:
            count = len(json.loads(row["tasks"] or "[]"))
        except json.JSONDecodeError:
            count = 0
        days.append({
            "id": row["id"],
            "snippet": (row["text"] or "").strip()[:80],
            "status": row["status"],
            "task_count": count,
            "created_at": row["created_at"],
        })
    return days


def save_dictation(dictation_id: str, session_id: str, raw_text: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO dictations (id, session_id, raw_text, polished_text, status, created_at) "
            "VALUES (?, ?, ?, ?, 'raw', ?)",
            (dictation_id, session_id, raw_text, raw_text, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_dictation_polish(
    dictation_id: str,
    session_id: str,
    polished_text: str,
    is_email: bool,
    email_to: str,
    email_subject: str,
    email_body: str,
) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE dictations SET polished_text = ?, is_email = ?, email_to = ?, "
            "email_subject = ?, email_body = ?, status = 'polished' "
            "WHERE id = ? AND session_id = ?",
            (polished_text, int(is_email), email_to, email_subject, email_body, dictation_id, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_dictation_sent(dictation_id: str, session_id: str, sent_to: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE dictations SET status = 'sent', sent_to = ? WHERE id = ? AND session_id = ?",
            (sent_to, dictation_id, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_dictation(dictation_id: str, session_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, raw_text, polished_text, is_email, email_to, email_subject, "
            "email_body, status, sent_to, created_at FROM dictations "
            "WHERE id = ? AND session_id = ?",
            (dictation_id, session_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "raw_text": row["raw_text"],
        "polished_text": row["polished_text"],
        "is_email": bool(row["is_email"]),
        "email_to": row["email_to"] or "",
        "email_subject": row["email_subject"] or "",
        "email_body": row["email_body"] or "",
        "status": row["status"],
        "sent_to": row["sent_to"],
        "created_at": row["created_at"],
    }


def list_dictations(session_id: str, limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, raw_text, polished_text, is_email, status, created_at FROM dictations "
            "WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "snippet": (row["polished_text"] or row["raw_text"] or "").strip()[:80],
            "is_email": bool(row["is_email"]),
            "status": row["status"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def save_artifact(artifact_id: str, session_id: str, title: str, fmt: str, body: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO artifacts (id, session_id, title, format, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artifact_id, session_id, title, fmt, body, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

def _artifact_body(raw: str) -> dict | None:
    try:
        body = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        logger.warning("skipping malformed artifact record")
        return None
    if not isinstance(body, dict) or not body.get("sections"):
        return None
    return body

def get_artifact(artifact_id: str, session_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT id, title, format, body, created_at FROM artifacts "
            "WHERE id = ? AND session_id = ?",
            (artifact_id, session_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    body = _artifact_body(row["body"])
    if not body:
        return None
    return {
        "id": row["id"],
        "title": row["title"],
        "format": row["format"],
        "created_at": row["created_at"],
        **body,
    }

def list_artifacts(session_id: str, limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT id, title, format, body, created_at FROM artifacts WHERE session_id = ? "
            "ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    valid: list[dict] = []
    for row in rows:
        if _artifact_body(row["body"]):
            valid.append({key: row[key] for key in ("id", "title", "format", "created_at")})
            if len(valid) == limit:
                break
    return valid
