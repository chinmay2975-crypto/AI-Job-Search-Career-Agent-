from agents.state import AutonomousState
from db import get_repository
from services.ats_discovery import DEFAULT_PLATFORMS, discover_jobs
from services.safety_rails import check_idempotent


def run(state: AutonomousState) -> AutonomousState:
    result = discover_jobs(
        query=state.get("query", ""),
        location=state.get("location", ""),
        platforms=tuple(state.get("platforms") or DEFAULT_PLATFORMS),
        include_remote=state.get("include_remote", False),
        internship_only=state.get("internship_only", False),
    )

    # Skip jobs this candidate already has a live application for (submitted, unconfirmed, awaiting review).
    repo = get_repository()
    fresh = []
    for job in result["jobs"]:
        existing = repo.get_job_by_url(job["url"])
        if existing and not check_idempotent(repo, existing["id"], state["candidate_id"]):
            continue
        fresh.append(job)

    return {**state, "discovered_jobs": fresh, "discovery_errors": result["errors"], "discovery_done": True}
