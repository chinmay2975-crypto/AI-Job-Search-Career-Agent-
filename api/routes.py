import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from agents.graph import build_graph

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

    return {
        "candidate": result.get("candidate"),
        "matches": result.get("matches", []),
        "skill_gaps": result.get("skill_gaps", []),
    }
