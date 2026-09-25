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

# submitter status -> application_events event_type
_EVENT_FOR_STATUS = {
    "submitted": "submit_confirmed",
    "dry_run": "submit_attempted",
    "needs_manual": "needs_manual",
    "unconfirmed": "submit_unconfirmed",
    "failed": "submit_failed",
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

    submit = not is_dry_run()
    if submit and not check_daily_cap(repo, state["candidate_id"]):
        repo.update_application(application_id, {"status": "failed", "error_log": "daily auto-submit cap reached"})
        repo.add_application_event(application_id, "submit_failed", "daily auto-submit cap reached")
        return {**state, "status": "failed"}

    submit_fn = getattr(playwright_apply, _AUTO_SUBMIT_FUNC_NAMES.get(ats_type, ""), None)
    if submit_fn is None:
        repo.update_application(application_id, {"status": "failed", "error_log": f"no submitter for {ats_type}"})
        repo.add_application_event(application_id, "submit_failed", f"no submitter for {ats_type}")
        return {**state, "status": "failed"}

    # The submitter itself decides whether to click Submit, based on ctx["submit"]: in DRY_RUN it
    # still fills the real form and screenshots it, so selectors are verified without sending anything.
    ctx = {
        "application_id": application_id,
        "profile": state.get("profile") or {},
        "resume_path": state.get("resume_path"),
        "submit": submit,
    }
    result = await submit_fn(state.get("job", {}), state.get("candidate", {}), state.get("cover_letter_text", ""), ctx)

    status = result["status"]
    detail = result.get("detail", "")
    if result.get("screenshots"):
        detail = f"{detail} | screenshots: {', '.join(s for s in result['screenshots'] if s)}"

    fields = {"status": status}
    if status == "submitted":
        fields.update(application_url=result.get("application_url", ""), submitted_at=_now())
    else:
        fields["error_log"] = detail  # the reason + screenshot paths, shown in the Applications tab
    repo.update_application(application_id, fields)
    repo.add_application_event(application_id, _EVENT_FOR_STATUS.get(status, status), detail)

    return {**state, "status": status, "submit_detail": detail}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
