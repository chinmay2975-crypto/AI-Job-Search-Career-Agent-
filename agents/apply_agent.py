from agents.state import AutonomousState
from services import application_runner, audit_log


async def run(state: AutonomousState) -> AutonomousState:
    """Apply to the next queued job through the shared per-application flow, then log it."""
    queue = list(state.get("apply_queue") or [])
    entry = queue.pop(0)
    job = entry["job"]
    score = entry["overall_score"]
    candidate_id = state["candidate_id"]

    try:
        outcome = await application_runner.start_application(
            candidate_id, job, score, resume_path=state.get("resume_path"), profile=state.get("profile")
        )
    except application_runner.DuplicateApplicationError as e:
        outcome = {"application_id": "", "status": "skipped_duplicate", "detail": str(e)}
    except Exception as e:  # one broken posting must not stop the rest of the run
        outcome = {"application_id": "", "status": "failed", "detail": f"{type(e).__name__}: {e}"}

    audit_log.record(job, outcome["status"], score, outcome.get("application_id", ""), outcome.get("detail", ""))

    result = {
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "platform": job.get("platform", ""),
        "url": job.get("url", ""),
        "score": score,
        **outcome,
    }
    return {**state, "apply_queue": queue, "results": [*(state.get("results") or []), result]}
