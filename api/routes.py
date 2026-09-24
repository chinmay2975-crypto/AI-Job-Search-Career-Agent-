import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from agents.graph import build_graph
from db import get_repository

router = APIRouter()
_graph = build_graph()


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
    }

    config = {"configurable": {"thread_id": candidate_id or str(uuid.uuid4())}}
    result = _graph.invoke(initial_state, config=config)

    candidate = result.get("candidate")
    if candidate:
        get_repository().save_candidate(candidate_id, candidate)

    return {
        "candidate": candidate,
        "matches": result.get("matches", []),
        "skill_gaps": result.get("skill_gaps", []),
    }
