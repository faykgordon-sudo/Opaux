"""
database.py -- SQLite database initialization and helper functions for Opaux.
"""

import hashlib
import os
import sqlite3
from datetime import datetime
from typing import Any

CREATE_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    url TEXT,
    source TEXT,
    description TEXT,
    salary_min REAL,
    salary_max REAL,
    job_type TEXT,
    is_remote BOOLEAN,
    date_posted TEXT,
    date_discovered TEXT,
    score REAL,
    score_reasoning TEXT,
    status TEXT DEFAULT 'discovered',
    cv_path TEXT,
    cover_letter_path TEXT,
    applied_date TEXT,
    response_date TEXT,
    notes TEXT,
    keywords_matched TEXT,
    ats_score REAL,
    cv_lang TEXT DEFAULT 'en',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_UPDATED_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS update_jobs_timestamp
AFTER UPDATE ON jobs
BEGIN
    UPDATE jobs SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
"""


def init_db(db_path: str) -> None:
    """Create the database tables if they don't exist, and run migrations."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_JOBS_TABLE)
        conn.execute(CREATE_UPDATED_TRIGGER)
        # Migration: add cv_lang column if missing (for existing databases)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "cv_lang" not in existing:
            conn.execute("ALTER TABLE jobs ADD COLUMN cv_lang TEXT DEFAULT 'en'")
        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a sqlite3 connection with row_factory set to Row."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def generate_job_id(url: str) -> str:
    """Generate a deterministic 16-char ID from the job URL using MD5."""
    return hashlib.md5(url.encode()).hexdigest()[:16]


def insert_job(conn: sqlite3.Connection, job: dict) -> None:
    """Upsert a job record by id. Existing rows are not overwritten."""
    columns = [
        "id", "title", "company", "location", "url", "source", "description",
        "salary_min", "salary_max", "job_type", "is_remote", "date_posted",
        "date_discovered", "score", "score_reasoning", "status", "cv_path",
        "cover_letter_path", "applied_date", "response_date", "notes",
        "keywords_matched", "ats_score",
    ]
    placeholders = ", ".join(["?" for _ in columns])
    col_names = ", ".join(columns)
    values = [job.get(col) for col in columns]

    conn.execute(
        f"""
        INSERT OR IGNORE INTO jobs ({col_names})
        VALUES ({placeholders})
        """,
        values,
    )
    conn.commit()


def get_unscored_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Return all jobs where score IS NULL (not yet scored)."""
    cursor = conn.execute(
        "SELECT * FROM jobs WHERE score IS NULL ORDER BY date_discovered DESC"
    )
    return [dict(row) for row in cursor.fetchall()]


def get_job_by_id(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """Return a single job dict by its ID, or None if not found."""
    cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def update_job(conn: sqlite3.Connection, job_id: str, **kwargs: Any) -> None:
    """Update arbitrary fields on a job record."""
    if not kwargs:
        return
    set_clause = ", ".join([f"{k} = ?" for k in kwargs])
    values = list(kwargs.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)
    conn.commit()


def get_all_jobs(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    """Return all jobs, optionally filtered by status."""
    if status:
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY score DESC NULLS LAST, created_at DESC",
            (status,),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM jobs ORDER BY score DESC NULLS LAST, created_at DESC"
        )
    return [dict(row) for row in cursor.fetchall()]
