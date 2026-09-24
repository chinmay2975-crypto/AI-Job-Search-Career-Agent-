from langgraph.types import interrupt

from agents.state import ApplicationState
from db import get_repository


def run(state: ApplicationState) -> ApplicationState:
    """Pauses the graph until POST /applications/{id}/approve or /reject resumes it
    with Command(resume={"approved": bool, "cover_letter_text": str | None})."""
    repo = get_repository()
    application_id = state["application_id"]
    current = repo.get_application(application_id)
    if current and current.get("status") == "draft":
        # Node functions re-run from the top on resume; only log/transition on the first pass.
        repo.update_application(application_id, {"status": "pending_approval"})
        repo.add_application_event(application_id, "pending_approval", "awaiting human review")

    decision = interrupt(
        {
            "application_id": state.get("application_id"),
            "job": state.get("job"),
            "cover_letter_text": state.get("cover_letter_text"),
            "ats_type": state.get("ats_type"),
            "execution_strategy": state.get("execution_strategy"),
        }
    )

    approved = bool(decision.get("approved", False))
    cover_letter_text = decision.get("cover_letter_text") or state.get("cover_letter_text", "")

    return {
        **state,
        "human_approved": approved,
        "cover_letter_text": cover_letter_text,
        "status": "approved" if approved else "rejected_by_user",
    }
