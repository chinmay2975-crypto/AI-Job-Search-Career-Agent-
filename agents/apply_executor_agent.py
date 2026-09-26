from datetime import datetime, timezone

from agents.state import ApplicationState
from db import get_repository
from services import playwright_apply
from services.safety_rails import is_dry_run

_AUTO_SUBMIT_FUNC_NAMES = {
    "synthetic": "submit_synthetic_application",
    "greenhouse": "submit_greenhouse_application",
    "lever": "submit_lever_application",
    "ashby": "submit_ashby_application",
    "workable": "submit_workable_application",
}

# submitter status -> application_events event_type
_EVENT_FOR_STATUS = {
    "submitted": "submit_confirmed",
    "dry_run": "submit_attempted",
    "needs_manual": "needs_manual",
    "verification_required": "verification_required",
    "unconfirmed": "submit_unconfirmed",
    "failed": "submit_failed",
}


async def run(state: ApplicationState) -> ApplicationState:
    application_id = state["application_id"]
    ats_type = state.get("ats_type", "")
    strategy = state.get("execution_strategy", "")

    if strategy != "auto_submit":
        repo = get_repository()
        # assisted_draft (workday/indeed): no automated site interaction, ever.
        # The human approved the draft; they submit it manually on the real site.
        repo.update_application(application_id, {"status": "approved"})
        repo.add_application_event(application_id, "approved", "assisted_draft: ready for manual submission")
        return {**state, "status": "approved"}

    status, detail = await execute_submission(
        application_id=application_id,
        candidate_id=state["candidate_id"],
        ats_type=ats_type,
        job=state.get("job", {}),
        candidate=state.get("candidate", {}),
        cover_letter_text=state.get("cover_letter_text", ""),
        resume_path=state.get("resume_path"),
        profile=state.get("profile") or {},
    )
    return {**state, "status": status, "submit_detail": detail}


async def execute_submission(
    application_id: str, candidate_id: str, ats_type: str, job: dict, candidate: dict, cover_letter_text: str,
    resume_path: str | None, profile: dict, overrides: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Run the platform submitter and record the outcome. Shared by the graph and by approvals."""
    repo = get_repository()
    submit_fn = getattr(playwright_apply, _AUTO_SUBMIT_FUNC_NAMES.get(ats_type, ""), None)
    if submit_fn is None:
        repo.update_application(application_id, {"status": "failed", "error_log": f"no submitter for {ats_type}"})
        repo.add_application_event(application_id, "submit_failed", f"no submitter for {ats_type}")
        return "failed", f"no submitter for {ats_type}"

    # The submitter itself decides whether to click Submit, based on ctx["submit"]: in DRY_RUN it
    # still fills the real form and screenshots it, so selectors are verified without sending anything.
    ctx = {
        "application_id": application_id,
        "profile": profile,
        "resume_path": resume_path,
        "submit": not is_dry_run(),
        "overrides": overrides or {},
        "saved_answers": repo.get_saved_answers(candidate_id),
    }
    result = await submit_fn(job, candidate, cover_letter_text, ctx)

    status = result["status"]
    detail = result.get("detail", "")
    if result.get("screenshots"):
        detail = f"{detail} | screenshots: {', '.join(s for s in result['screenshots'] if s)}"

    fields = {"status": status, "pending_questions": result.get("pending_questions") or None}
    if status == "submitted":
        fields.update(application_url=result.get("application_url", ""), submitted_at=_now(), error_log="")
    else:
        fields["error_log"] = detail  # the reason + screenshot paths, shown in the Applications tab
    repo.update_application(application_id, fields)
    repo.add_application_event(application_id, _EVENT_FOR_STATUS.get(status, status), detail)
    return status, detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
