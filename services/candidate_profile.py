"""Load the user's candidate_profile.yaml - the only source for personal/eligibility answers."""

from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "candidate_profile.yaml"


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    profile_path = Path(path) if path else DEFAULT_PROFILE_PATH
    if not profile_path.exists():
        raise FileNotFoundError(
            f"{profile_path} not found. Copy candidate_profile.example.yaml to candidate_profile.yaml "
            "and fill it in - it answers the application questions your resume can't."
        )

    with profile_path.open(encoding="utf-8") as f:
        profile = yaml.safe_load(f) or {}

    profile.setdefault("eeo", {})
    profile.setdefault("custom_answers", [])
    return profile


def full_name(profile: dict[str, Any]) -> str:
    return f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()


def missing_required_fields(profile: dict[str, Any]) -> list[str]:
    """Fields every ATS form requires; a profile without them can't produce a valid application."""
    return [f for f in ("first_name", "last_name", "email", "phone") if not str(profile.get(f, "")).strip()]
