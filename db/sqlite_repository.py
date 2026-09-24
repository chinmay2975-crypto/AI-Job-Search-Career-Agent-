import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.repository import Repository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (candidate_id TEXT PRIMARY KEY, profile TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS education (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weak_areas (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS career_preferences (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_checkpoints (thread_id TEXT PRIMARY KEY, data TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT,
    url TEXT NOT NULL UNIQUE,
    description TEXT,
    location TEXT,
    ats_type TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    match_score REAL,
    ats_type TEXT,
    execution_strategy TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    cover_letter_text TEXT,
    thread_id TEXT,
    submitted_at TEXT,
    application_url TEXT,
    error_log TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_events (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""

_APPLICATION_COLUMNS = [
    "id", "job_id", "candidate_id", "match_score", "ats_type", "execution_strategy",
    "status", "cover_letter_text", "thread_id", "submitted_at", "application_url",
    "error_log", "created_at",
]

_JOB_COLUMNS = ["id", "title", "company", "url", "description", "location", "ats_type", "source", "created_at"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row, columns: list[str]) -> dict[str, Any]:
    return {col: row[col] for col in columns}


class SQLiteRepository(Repository):
    def __init__(self, db_path: str = "career_agent.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _get_json(self, table: str, key_col: str, key: str) -> Any | None:
        row = self._conn.execute(f"SELECT data FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _save_json(self, table: str, key_col: str, key: str, value: Any) -> None:
        self._conn.execute(
            f"INSERT INTO {table} ({key_col}, data) VALUES (?, ?) "
            f"ON CONFLICT({key_col}) DO UPDATE SET data = excluded.data",
            (key, json.dumps(value)),
        )
        self._conn.commit()

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT profile FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_candidate(self, candidate_id: str, profile: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO candidates (candidate_id, profile) VALUES (?, ?) "
            "ON CONFLICT(candidate_id) DO UPDATE SET profile = excluded.profile",
            (candidate_id, json.dumps(profile)),
        )
        self._conn.commit()

    def get_skills(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_json("skills", "candidate_id", candidate_id) or []

    def save_skills(self, candidate_id: str, skills: list[dict[str, Any]]) -> None:
        self._save_json("skills", "candidate_id", candidate_id, skills)

    def get_education(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_json("education", "candidate_id", candidate_id) or []

    def save_education(self, candidate_id: str, education: list[dict[str, Any]]) -> None:
        self._save_json("education", "candidate_id", candidate_id, education)

    def get_projects(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_json("projects", "candidate_id", candidate_id) or []

    def save_projects(self, candidate_id: str, projects: list[dict[str, Any]]) -> None:
        self._save_json("projects", "candidate_id", candidate_id, projects)

    def get_applications(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT data FROM candidate_applications WHERE candidate_id = ?", (candidate_id,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_application(self, candidate_id: str, application: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO candidate_applications (candidate_id, data) VALUES (?, ?)",
            (candidate_id, json.dumps(application)),
        )
        self._conn.commit()

    def get_weak_areas(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_json("weak_areas", "candidate_id", candidate_id) or []

    def save_weak_areas(self, candidate_id: str, weak_areas: list[dict[str, Any]]) -> None:
        self._save_json("weak_areas", "candidate_id", candidate_id, weak_areas)

    def get_career_preferences(self, candidate_id: str) -> dict[str, Any] | None:
        return self._get_json("career_preferences", "candidate_id", candidate_id)

    def save_career_preferences(self, candidate_id: str, preferences: dict[str, Any]) -> None:
        self._save_json("career_preferences", "candidate_id", candidate_id, preferences)

    def get_graph_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        return self._get_json("graph_checkpoints", "thread_id", thread_id)

    def save_graph_checkpoint(self, thread_id: str, checkpoint: dict[str, Any]) -> None:
        self._save_json("graph_checkpoints", "thread_id", thread_id, checkpoint)

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_job_by_url(job["url"])
        if existing:
            return existing

        row = {
            "id": job.get("id") or str(uuid.uuid4()),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "url": job["url"],
            "description": job.get("description", ""),
            "location": job.get("location", ""),
            "ats_type": job.get("ats_type", ""),
            "source": job.get("source", "serper"),
            "created_at": _now(),
        }
        self._conn.execute(
            "INSERT INTO jobs (id, title, company, url, description, location, ats_type, source, created_at) "
            "VALUES (:id, :title, :company, :url, :description, :location, :ats_type, :source, :created_at)",
            row,
        )
        self._conn.commit()
        return row

    def get_job_by_url(self, url: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        return _row_to_dict(row, _JOB_COLUMNS) if row else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_dict(row, _JOB_COLUMNS) if row else None

    def create_application(self, application: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": application.get("id") or str(uuid.uuid4()),
            "job_id": application["job_id"],
            "candidate_id": application["candidate_id"],
            "match_score": application.get("match_score"),
            "ats_type": application.get("ats_type", ""),
            "execution_strategy": application.get("execution_strategy", ""),
            "status": application.get("status", "draft"),
            "cover_letter_text": application.get("cover_letter_text", ""),
            "thread_id": application.get("thread_id", ""),
            "submitted_at": application.get("submitted_at"),
            "application_url": application.get("application_url", ""),
            "error_log": application.get("error_log", ""),
            "created_at": _now(),
        }
        self._conn.execute(
            "INSERT INTO applications (id, job_id, candidate_id, match_score, ats_type, execution_strategy, "
            "status, cover_letter_text, thread_id, submitted_at, application_url, error_log, created_at) "
            "VALUES (:id, :job_id, :candidate_id, :match_score, :ats_type, :execution_strategy, :status, "
            ":cover_letter_text, :thread_id, :submitted_at, :application_url, :error_log, :created_at)",
            row,
        )
        self._conn.commit()
        return row

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
        return _row_to_dict(row, _APPLICATION_COLUMNS) if row else None

    def update_application(self, application_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{col} = :{col}" for col in fields)
        params = {**fields, "id": application_id}
        self._conn.execute(f"UPDATE applications SET {set_clause} WHERE id = :id", params)
        self._conn.commit()

    def list_applications(
        self, candidate_id: str | None = None, status: str | None = None, min_score: float | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM applications WHERE 1=1"
        params: dict[str, Any] = {}
        if candidate_id:
            query += " AND candidate_id = :candidate_id"
            params["candidate_id"] = candidate_id
        if status:
            query += " AND status = :status"
            params["status"] = status
        if min_score is not None:
            query += " AND match_score >= :min_score"
            params["min_score"] = min_score
        query += " ORDER BY created_at DESC"

        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_dict(r, _APPLICATION_COLUMNS) for r in rows]

    def get_non_failed_application_for_job(self, job_id: str, candidate_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM applications WHERE job_id = ? AND candidate_id = ? AND status != 'failed' "
            "ORDER BY created_at DESC LIMIT 1",
            (job_id, candidate_id),
        ).fetchone()
        return _row_to_dict(row, _APPLICATION_COLUMNS) if row else None

    def add_application_event(self, application_id: str, event_type: str, detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO application_events (id, application_id, event_type, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), application_id, event_type, detail, _now()),
        )
        self._conn.commit()

    def list_application_events(self, application_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event_type, detail, created_at FROM application_events "
            "WHERE application_id = ? ORDER BY created_at",
            (application_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_auto_submits_today(self, candidate_id: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM applications "
            "WHERE candidate_id = ? AND execution_strategy = 'auto_submit' "
            "AND status = 'submitted' AND date(submitted_at) = ?",
            (candidate_id, today),
        ).fetchone()
        return row["cnt"] if row else 0
