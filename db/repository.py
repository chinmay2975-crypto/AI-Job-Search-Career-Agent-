from abc import ABC, abstractmethod
from typing import Any


class Repository(ABC):
    """Persistence interface for candidate state. Implementations: SQLite (local dev), Supabase (prod)."""

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
