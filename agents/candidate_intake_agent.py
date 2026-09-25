from pathlib import Path

from agents import resume_agent
from agents.state import AutonomousState
from db import get_repository


def run(state: AutonomousState) -> AutonomousState:
    """Parse the resume file with the existing Resume Agent and persist the candidate profile."""
    resume_bytes = Path(state["resume_path"]).read_bytes()
    parsed = resume_agent.run({"candidate_id": state["candidate_id"], "resume_bytes": resume_bytes})
    candidate = parsed["candidate"]
    get_repository().save_candidate(state["candidate_id"], candidate)

    profile = state.get("profile") or {}
    query = state.get("query") or profile.get("current_title") or " ".join(candidate.get("skills", [])[:2])
    return {**state, "candidate": candidate, "query": query}
