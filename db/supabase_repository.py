from datetime import datetime, timezone
from typing import Any

from db.repository import Repository
from db.supabase_client import get_client

_PROFILE_SUBKEYS = ["skills", "education_level", "years_experience", "projects", "location"]


class SupabaseRepository(Repository):
    """Postgres/Supabase implementation of Repository.

    Candidate profile fields (skills, education, projects, weak_areas, career_preferences) are
    kept inside candidates.resume_metadata as one jsonb blob rather than one table per field,
    per db/migrations/0001_init.sql.
    """

    def __init__(self):
        self._client = get_client()

    def _get_profile_blob(self, candidate_id: str) -> dict[str, Any]:
        row = self._client.table("candidates").select("resume_metadata").eq("id", candidate_id).limit(1).execute()
        if row.data and row.data[0].get("resume_metadata"):
            return row.data[0]["resume_metadata"]
        return {}

    def _merge_profile_blob(self, candidate_id: str, patch: dict[str, Any]) -> None:
        blob = self._get_profile_blob(candidate_id)
        blob.update(patch)
        exists = self._client.table("candidates").select("id").eq("id", candidate_id).limit(1).execute()
        if exists.data:
            self._client.table("candidates").update({"resume_metadata": blob}).eq("id", candidate_id).execute()
        else:
            self._client.table("candidates").insert({"id": candidate_id, "resume_metadata": blob}).execute()

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._client.table("candidates").select("*").eq("id", candidate_id).limit(1).execute()
        if not row.data:
            return None
        record = row.data[0]
        # Flatten resume_metadata so this matches SQLiteRepository's flat profile shape.
        profile = {
            "candidate_id": candidate_id,
            "resume_text": record.get("resume_text", ""),
            **(record.get("resume_metadata") or {}),
        }
        return profile

    def save_candidate(self, candidate_id: str, profile: dict[str, Any]) -> None:
        payload = {
            "id": candidate_id,
            "name": profile.get("name", ""),
            "resume_text": profile.get("resume_text", ""),
        }
        exists = self._client.table("candidates").select("id").eq("id", candidate_id).limit(1).execute()
        if exists.data:
            self._client.table("candidates").update(payload).eq("id", candidate_id).execute()
        else:
            self._client.table("candidates").insert(payload).execute()
        self._merge_profile_blob(candidate_id, {k: v for k, v in profile.items() if k in _PROFILE_SUBKEYS})

    def get_skills(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_profile_blob(candidate_id).get("skills", [])

    def save_skills(self, candidate_id: str, skills: list[dict[str, Any]]) -> None:
        self._merge_profile_blob(candidate_id, {"skills": skills})

    def get_education(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_profile_blob(candidate_id).get("education_level", [])

    def save_education(self, candidate_id: str, education: list[dict[str, Any]]) -> None:
        self._merge_profile_blob(candidate_id, {"education_level": education})

    def get_projects(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_profile_blob(candidate_id).get("projects", [])

    def save_projects(self, candidate_id: str, projects: list[dict[str, Any]]) -> None:
        self._merge_profile_blob(candidate_id, {"projects": projects})

    def get_applications(self, candidate_id: str) -> list[dict[str, Any]]:
        return self.list_applications(candidate_id=candidate_id)

    def save_application(self, candidate_id: str, application: dict[str, Any]) -> None:
        self.create_application({**application, "candidate_id": candidate_id})

    def get_weak_areas(self, candidate_id: str) -> list[dict[str, Any]]:
        return self._get_profile_blob(candidate_id).get("weak_areas", [])

    def save_weak_areas(self, candidate_id: str, weak_areas: list[dict[str, Any]]) -> None:
        self._merge_profile_blob(candidate_id, {"weak_areas": weak_areas})

    def get_career_preferences(self, candidate_id: str) -> dict[str, Any] | None:
        return self._get_profile_blob(candidate_id).get("career_preferences")

    def save_career_preferences(self, candidate_id: str, preferences: dict[str, Any]) -> None:
        self._merge_profile_blob(candidate_id, {"career_preferences": preferences})

    def get_graph_checkpoint(self, thread_id: str) -> dict[str, Any] | None:
        row = self._client.table("graph_checkpoints").select("data").eq("thread_id", thread_id).limit(1).execute()
        return row.data[0]["data"] if row.data else None

    def save_graph_checkpoint(self, thread_id: str, checkpoint: dict[str, Any]) -> None:
        self._client.table("graph_checkpoints").upsert(
            {"thread_id": thread_id, "data": checkpoint, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).execute()

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_job_by_url(job["url"])
        if existing:
            return existing
        payload = {k: job.get(k, "") for k in ("title", "company", "url", "description", "location", "ats_type")}
        payload["source"] = job.get("source", "serper")
        result = self._client.table("jobs").insert(payload).execute()
        return result.data[0]

    def get_job_by_url(self, url: str) -> dict[str, Any] | None:
        row = self._client.table("jobs").select("*").eq("url", url).limit(1).execute()
        return row.data[0] if row.data else None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._client.table("jobs").select("*").eq("id", job_id).limit(1).execute()
        return row.data[0] if row.data else None

    def create_application(self, application: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "job_id": application["job_id"],
            "candidate_id": application["candidate_id"],
            "match_score": application.get("match_score"),
            "ats_type": application.get("ats_type", ""),
            "execution_strategy": application.get("execution_strategy", ""),
            "status": application.get("status", "draft"),
            "cover_letter_text": application.get("cover_letter_text", ""),
            "thread_id": application.get("thread_id", ""),
            "application_url": application.get("application_url", ""),
            "error_log": application.get("error_log", ""),
        }
        if application.get("id"):
            # Caller (e.g. /applications/start) may pre-generate the id so it can use the same
            # value as thread_id before the row exists - respect it instead of letting
            # Postgres assign a different one.
            payload["id"] = application["id"]
        result = self._client.table("applications").insert(payload).execute()
        return result.data[0]

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        row = self._client.table("applications").select("*").eq("id", application_id).limit(1).execute()
        return row.data[0] if row.data else None

    def update_application(self, application_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        self._client.table("applications").update(fields).eq("id", application_id).execute()

    def list_applications(
        self, candidate_id: str | None = None, status: str | None = None, min_score: float | None = None
    ) -> list[dict[str, Any]]:
        query = self._client.table("applications").select("*")
        if candidate_id:
            query = query.eq("candidate_id", candidate_id)
        if status:
            query = query.eq("status", status)
        if min_score is not None:
            query = query.gte("match_score", min_score)
        result = query.order("created_at", desc=True).execute()
        return result.data

    def get_non_failed_application_for_job(self, job_id: str, candidate_id: str) -> dict[str, Any] | None:
        result = (
            self._client.table("applications")
            .select("*")
            .eq("job_id", job_id)
            .eq("candidate_id", candidate_id)
            .neq("status", "failed")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    def add_application_event(self, application_id: str, event_type: str, detail: str = "") -> None:
        self._client.table("application_events").insert(
            {"application_id": application_id, "event_type": event_type, "detail": detail}
        ).execute()

    def list_application_events(self, application_id: str) -> list[dict[str, Any]]:
        result = (
            self._client.table("application_events")
            .select("event_type,detail,created_at")
            .eq("application_id", application_id)
            .order("created_at")
            .execute()
        )
        return result.data

    def count_auto_submits_today(self, candidate_id: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        result = (
            self._client.table("applications")
            .select("id", count="exact")
            .eq("candidate_id", candidate_id)
            .eq("execution_strategy", "auto_submit")
            .eq("status", "submitted")
            .gte("submitted_at", f"{today}T00:00:00")
            .execute()
        )
        return result.count or 0
