import random
import string

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import get_repository
from services import application_runner

router = APIRouter()


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
    try:
        return await application_runner.start_application(req.candidate_id, req.job, req.match_score)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except application_runner.DuplicateApplicationError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/applications/{application_id}/approve")
async def approve_application(application_id: str, req: ApproveRequest):
    if not get_repository().get_application(application_id):
        raise HTTPException(status_code=404, detail="application not found")
    return await application_runner.approve_application(application_id, req.cover_letter_text)


@router.post("/applications/{application_id}/reject")
async def reject_application(application_id: str):
    if not get_repository().get_application(application_id):
        raise HTTPException(status_code=404, detail="application not found")
    return await application_runner.reject_application(application_id)


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
