from datetime import datetime, timezone

from agents.state import ApplicationState
from db import get_repository
from services import playwright_apply
from services.safety_rails import check_daily_cap, is_dry_run

_AUTO_SUBMIT_FUNC_NAMES = {
    "synthetic": "submit_synthetic_application",
    "greenhouse": "submit_greenhouse_application",
    "lever": "submit_lever_application",
}


async def run(state: ApplicationState) -> ApplicationState:
    repo = get_repository()
    application_id = state["application_id"]
    ats_type = state.get("ats_type", "")
    strategy = state.get("execution_strategy", "")

    if strategy != "auto_submit":
        # assisted_draft (workday/indeed): no automated site interaction, ever.
        # The human approved the draft; they submit it manually on the real site.
        repo.update_application(application_id, {"status": "approved"})
        repo.add_application_event(application_id, "approved", "assisted_draft: ready for manual submission")
        return {**state, "status": "approved"}

    if not check_daily_cap(repo, state["candidate_id"]):
        repo.update_application(application_id, {"status": "failed", "error_log": "daily auto-submit cap reached"})
        repo.add_application_event(application_id, "submit_failed", "daily auto-submit cap reached")
        return {**state, "status": "failed"}

    if is_dry_run():
        repo.update_application(application_id, {"status": "approved"})
        repo.add_application_event(application_id, "submit_attempted", "DRY_RUN=true: real submission skipped")
        return {**state, "status": "approved"}

    submit_fn = getattr(playwright_apply, _AUTO_SUBMIT_FUNC_NAMES.get(ats_type, ""), None)
    repo.add_application_event(application_id, "submit_attempted", f"ats_type={ats_type}")
    result = await submit_fn(state.get("job", {}), state.get("candidate", {}), state.get("cover_letter_text", ""))

    if result["success"]:
        repo.update_application(
            application_id,
            {"status": "submitted", "application_url": result["application_url"], "submitted_at": _now()},
        )
        repo.add_application_event(application_id, "submit_confirmed", result["application_url"])
        return {**state, "status": "submitted"}

    repo.update_application(application_id, {"status": "failed", "error_log": result["error"]})
    repo.add_application_event(application_id, "submit_failed", result["error"])
    return {**state, "status": "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
