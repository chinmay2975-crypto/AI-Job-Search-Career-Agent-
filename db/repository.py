from abc import ABC, abstractmethod
from typing import Any

# Application statuses where nothing was sent to the employer, so the job may be tried again.
# Every other status (submitted, unconfirmed, pending_approval, approved, blocked, ...) blocks a re-apply.
# "superseded" marks an earlier attempt replaced by a newer one for the same job.
# "verification_required" also sent nothing but is deliberately not retried: every attempt can make the
# site email the user another security code.
RETRYABLE_STATUSES = ("failed", "dry_run", "needs_manual", "superseded")


class Repository(ABC):
    """Persistence interface for candidate and application state. Implementation: SQLiteRepository."""

    @abstractmethod
    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_candidate(self, candidate_id: str, profile: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_skills(self, candidate_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_skills(self, candidate_id: str, skills: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def get_education(self, candidate_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_education(self, candidate_id: str, education: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def get_projects(self, candidate_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_projects(self, candidate_id: str, projects: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def get_applications(self, candidate_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_application(self, candidate_id: str, application: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_weak_areas(self, candidate_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_weak_areas(self, candidate_id: str, weak_areas: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def get_career_preferences(self, candidate_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_career_preferences(self, candidate_id: str, preferences: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_graph_checkpoint(self, thread_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def save_graph_checkpoint(self, thread_id: str, checkpoint: dict[str, Any]) -> None: ...

    @abstractmethod
    def create_job(self, job: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def get_job_by_url(self, url: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def create_application(self, application: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def get_application(self, application_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def update_application(self, application_id: str, fields: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_applications(
        self, candidate_id: str | None = None, status: str | None = None, min_score: float | None = None
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_blocking_application_for_job(self, job_id: str, candidate_id: str) -> dict[str, Any] | None:
        """Latest application for this job/candidate whose status is not in RETRYABLE_STATUSES."""

    @abstractmethod
    def add_application_event(self, application_id: str, event_type: str, detail: str = "") -> None: ...

    @abstractmethod
    def list_application_events(self, application_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def supersede_retryable_applications(self, job_id: str, candidate_id: str) -> None:
        """Mark earlier not-sent attempts for this job as superseded before a new attempt starts."""

    @abstractmethod
    def get_saved_answers(self, candidate_id: str) -> dict[str, str]:
        """Answers the user gave while approving held applications, keyed by normalized question text."""

    @abstractmethod
    def save_answer(self, candidate_id: str, question_label: str, answer: str) -> None: ...
