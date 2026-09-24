from agents.state import ApplicationState
from db import get_repository
from services import ats_classifier
from services.safety_rails import match_score_threshold


def run(state: ApplicationState) -> ApplicationState:
    repo = get_repository()
    application_id = state["application_id"]
    job = state.get("job", {})

    ats_type = ats_classifier.classify(job.get("link") or job.get("url", ""))
    strategy = ats_classifier.execution_strategy(ats_type)
    below_threshold = state.get("match_score", 0) < match_score_threshold()

    repo.update_application(application_id, {"ats_type": ats_type, "execution_strategy": strategy})

    if strategy == "blocked":
        detail = "LinkedIn posting: handle manually, no agent action taken" if ats_type == "linkedin" else f"unclassifiable ATS ({ats_type})"
        repo.update_application(application_id, {"status": "blocked"})
        repo.add_application_event(application_id, "blocked", detail)
    elif below_threshold:
        repo.update_application(application_id, {"status": "blocked"})
        repo.add_application_event(application_id, "blocked", "match score below threshold")

    return {**state, "ats_type": ats_type, "execution_strategy": strategy, "below_threshold": below_threshold}
