from agents.state import GraphState
from services.job_search import search_jobs


def run(state: GraphState) -> GraphState:
    candidate = state.get("candidate", {})
    query = state.get("search_query") or " ".join(candidate.get("skills", [])[:5])
    location = state.get("location") or candidate.get("location", "")

    jobs = search_jobs(query=query, location=location)
    return {**state, "jobs": jobs}
