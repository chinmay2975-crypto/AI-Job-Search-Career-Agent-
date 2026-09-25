"""Start / approve / reject one application. Shared by the API and the autonomous Apply agent."""

import re
import uuid
from pathlib import Path

from langgraph.types import Command

from agents.application_graph import build_application_graph
from db import get_repository
from services.ats_classifier import classify
from services.candidate_profile import load_profile
from services.safety_rails import check_idempotent

RESUMES_DIR = Path(__file__).resolve().parent.parent / "logs" / "resumes"

app_graph = build_application_graph()


class DuplicateApplicationError(Exception):
    pass


def stored_resume_path(candidate_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", candidate_id) or "candidate"
    return RESUMES_DIR / f"{safe_id}.pdf"


def _default_profile() -> dict:
    try:
        return load_profile()
    except FileNotFoundError:
        return {}


async def start_application(
    candidate_id: str, job: dict, match_score: float, resume_path: str | None = None, profile: dict | None = None
) -> dict:
    repo = get_repository()

    job_url = job.get("link") or job.get("url", "")
    if not job_url:
        raise ValueError("job must include a link/url")

    job_row = repo.create_job(
        {**job, "url": job_url, "ats_type": job.get("ats_type") or classify(job_url), "source": job.get("source", "serper")}
    )
    if not check_idempotent(repo, job_row["id"], candidate_id):
        raise DuplicateApplicationError("an active application already exists for this job")

    if not resume_path and stored_resume_path(candidate_id).exists():
        resume_path = str(stored_resume_path(candidate_id))

    application_id = str(uuid.uuid4())
    repo.create_application(
        {
            "id": application_id,
            "job_id": job_row["id"],
            "candidate_id": candidate_id,
            "match_score": match_score,
            "thread_id": application_id,
            "status": "draft",
        }
    )
    repo.add_application_event(application_id, "started", "application pipeline started")

    initial_state = {
        "application_id": application_id,
        "candidate_id": candidate_id,
        "candidate": repo.get_candidate(candidate_id) or {},
        "job": job,
        "match_score": match_score,
        "resume_path": resume_path or "",
        "profile": profile if profile is not None else _default_profile(),
    }
    config = {"configurable": {"thread_id": application_id}}
    result = await app_graph.ainvoke(initial_state, config=config)
    return summarize(application_id, result)


def _has_paused_run(application_id: str) -> bool:
    config = {"configurable": {"thread_id": application_id}}
    return bool(app_graph.get_state(config).next)


def _approve_without_checkpoint(application_id: str, cover_letter_text: str | None) -> None:
    """The pause point is only reachable by assisted_draft jobs, whose post-approval step is purely
    bookkeeping - so it can be finished from the DB row when this process never held the run
    (e.g. it was started by the autonomous CLI) or restarted since."""
    repo = get_repository()
    fields = {"status": "approved"}
    if cover_letter_text:
        fields["cover_letter_text"] = cover_letter_text
    repo.update_application(application_id, fields)
    repo.add_application_event(application_id, "approved", "assisted_draft: ready for manual submission")


async def approve_application(application_id: str, cover_letter_text: str | None = None) -> dict:
    repo = get_repository()
    repo.add_application_event(application_id, "approved", cover_letter_text or "")
    if not _has_paused_run(application_id):
        _approve_without_checkpoint(application_id, cover_letter_text)
        return summarize(application_id, {})
    config = {"configurable": {"thread_id": application_id}}
    result = await app_graph.ainvoke(
        Command(resume={"approved": True, "cover_letter_text": cover_letter_text}), config=config
    )
    return summarize(application_id, result)


async def reject_application(application_id: str) -> dict:
    repo = get_repository()
    repo.update_application(application_id, {"status": "rejected_by_user"})
    repo.add_application_event(application_id, "rejected", "")
    if _has_paused_run(application_id):
        config = {"configurable": {"thread_id": application_id}}
        await app_graph.ainvoke(Command(resume={"approved": False}), config=config)
    return {"application_id": application_id, "status": "rejected_by_user"}


def summarize(application_id: str, result: dict) -> dict:
    application = get_repository().get_application(application_id) or {}
    status = "pending_approval" if "__interrupt__" in result else application.get("status") or result.get("status")
    return {
        "application_id": application_id,
        "status": status,
        "ats_type": application.get("ats_type", ""),
        "detail": application.get("error_log") or result.get("submit_detail", ""),
    }
