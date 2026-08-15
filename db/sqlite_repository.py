import json
import sqlite3
from pathlib import Path
from typing import Any

from db.repository import Repository

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (candidate_id TEXT PRIMARY KEY, profile TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS skills (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS education (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weak_areas (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS career_preferences (candidate_id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS graph_checkpoints (thread_id TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


class SQLiteRepository(Repository):
    def __init__(self, db_path: str = "career_agent.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
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
            "SELECT data FROM applications WHERE candidate_id = ?", (candidate_id,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_application(self, candidate_id: str, application: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO applications (candidate_id, data) VALUES (?, ?)",
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
