from agents.state import GraphState
from services.matching import score_match


def run(state: GraphState) -> GraphState:
    candidate = state.get("candidate", {})
    jobs = state.get("jobs", [])

    matches = []
    for job in jobs:
        result = score_match(candidate, job)
        matches.append({"job": job, **result})

    matches.sort(key=lambda m: m["overall_score"], reverse=True)
    return {**state, "matches": matches}
