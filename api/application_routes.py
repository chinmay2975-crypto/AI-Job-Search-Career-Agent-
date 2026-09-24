import random
import string
import uuid

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from agents.application_graph import build_application_graph
from db import get_repository
from services.safety_rails import check_idempotent

router = APIRouter()
_app_graph = build_application_graph()


class StartApplicationRequest(BaseModel):
    candidate_id: str
    job: dict
    match_score: float


class ApproveRequest(BaseModel):
    cover_letter_text: str | None = None


class SandboxApplyRequest(BaseModel):
    job_url: str
    candidate_id: str
    cover_letter_text: str


@router.post("/applications/start")
async def start_application(req: StartApplicationRequest):
    repo = get_repository()

    job_url = req.job.get("link") or req.job.get("url", "")
    if not job_url:
        raise HTTPException(status_code=400, detail="job must include a link/url")

    job_row = repo.create_job({**req.job, "url": job_url, "source": req.job.get("source", "serper")})

    if not check_idempotent(repo, job_row["id"], req.candidate_id):
        raise HTTPException(status_code=409, detail="an active application already exists for this job")

    candidate = repo.get_candidate(req.candidate_id) or {}
    application_id = str(uuid.uuid4())

    repo.create_application(
        {
            "id": application_id,
            "job_id": job_row["id"],
            "candidate_id": req.candidate_id,
            "match_score": req.match_score,
            "thread_id": application_id,
            "status": "draft",
        }
    )
    repo.add_application_event(application_id, "started", "application pipeline started")

    config = {"configurable": {"thread_id": application_id}}
    initial_state = {
        "application_id": application_id,
        "candidate_id": req.candidate_id,
        "candidate": candidate,
        "job": req.job,
        "match_score": req.match_score,
    }

    result = await _app_graph.ainvoke(initial_state, config=config)
    return _summarize(application_id, result, repo)


@router.post("/applications/{application_id}/approve")
async def approve_application(application_id: str, req: ApproveRequest):
    repo = get_repository()
    application = repo.get_application(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="application not found")

    repo.add_application_event(application_id, "approved", req.cover_letter_text or "")
    config = {"configurable": {"thread_id": application_id}}
    resume_payload = {"approved": True, "cover_letter_text": req.cover_letter_text}
    result = await _app_graph.ainvoke(Command(resume=resume_payload), config=config)
    return _summarize(application_id, result, repo)


@router.post("/applications/{application_id}/reject")
async def reject_application(application_id: str):
    repo = get_repository()
    application = repo.get_application(application_id)
    if not application:
        raise HTTPException(status_code=404, detail="application not found")

    repo.update_application(application_id, {"status": "rejected_by_user"})
    repo.add_application_event(application_id, "rejected", "")
    config = {"configurable": {"thread_id": application_id}}
    await _app_graph.ainvoke(Command(resume={"approved": False}), config=config)
    return {"application_id": application_id, "status": "rejected_by_user"}


@router.get("/applications")
def list_applications(candidate_id: str | None = None, status: str | None = None, min_score: float | None = None):
    repo = get_repository()
    applications = repo.list_applications(candidate_id=candidate_id, status=status, min_score=min_score)

    for application in applications:
        job = repo.get_job(application["job_id"])
        application["job_url"] = job.get("url", "") if job else ""
        application["job_title"] = job.get("title", "") if job else ""

        if application.get("status") == "blocked":
            events = repo.list_application_events(application["id"])
            blocked_events = [e for e in events if e["event_type"] == "blocked"]
            application["blocked_reason"] = blocked_events[-1]["detail"] if blocked_events else "blocked"

    return applications


@router.post("/sandbox/apply")
def sandbox_apply(req: SandboxApplyRequest):
    """Fake ATS 'apply' endpoint for the synthetic sandbox - always succeeds deterministically."""
    confirmation_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return {"status": "received", "confirmation_url": f"{req.job_url}?confirmation={confirmation_id}"}


def _summarize(application_id: str, result: dict, repo) -> dict:
    if "__interrupt__" in result:
        return {"application_id": application_id, "status": "pending_approval"}
    application = repo.get_application(application_id)
    return {"application_id": application_id, "status": application.get("status") if application else result.get("status")}
