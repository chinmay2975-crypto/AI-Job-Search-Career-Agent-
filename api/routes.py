import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from agents.graph import build_graph
from db import get_repository
from services.application_runner import stored_resume_path
from services.candidate_profile import load_profile

router = APIRouter()
_graph = build_graph()


def _profile_job_type() -> str:
    try:
        return str(load_profile().get("job_type") or "any").strip().lower()
    except FileNotFoundError:
        return "any"


@router.post("/pipeline/run")
async def run_pipeline(
    candidate_id: str = Form(...),
    search_query: str = Form(""),
    location: str = Form(""),
    resume: UploadFile = File(...),
):
    if resume.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="resume must be a PDF")

    resume_bytes = await resume.read()
    initial_state = {
        "candidate_id": candidate_id,
        "resume_bytes": resume_bytes,
        "search_query": search_query,
        "location": location,
        "internship_only": _profile_job_type() == "internship",
    }

    config = {"configurable": {"thread_id": candidate_id or str(uuid.uuid4())}}
    result = _graph.invoke(initial_state, config=config)

    candidate = result.get("candidate")
    if candidate:
        get_repository().save_candidate(candidate_id, candidate)
        # Kept locally so a later Apply can attach the resume file to ATS forms.
        resume_path = stored_resume_path(candidate_id)
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        resume_path.write_bytes(resume_bytes)

    return {
        "candidate": candidate,
        "matches": result.get("matches", []),
        "skill_gaps": result.get("skill_gaps", []),
    }
