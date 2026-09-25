from agents.state import AutonomousState
from services.ats_discovery import matches_location
from services.job_requirements import extract_requirements
from services.matching import score_match
from services.safety_rails import match_score_threshold

_DEFAULT_MAX_JOBS = 20


def run(state: AutonomousState) -> AutonomousState:
    candidate = state.get("candidate", {})
    location = state.get("location", "")
    threshold = match_score_threshold()

    scored = []
    for job in state.get("discovered_jobs", []):
        requirements = extract_requirements(job, candidate.get("skills", []))
        enriched = {**job, **requirements}

        # Discovery already filtered on location; score it as a match (or neutral for remote roles)
        # rather than letting score_match compare "Pune" to "India - Bengaluru; India - Pune" literally.
        scoring_job = {**enriched, "location": location if matches_location(job, location) else ""}
        result = score_match({**candidate, "location": location}, scoring_job)

        if not requirements["required_skills"]:
            # Without extracted skills, score_match would hand out the full skills credit.
            eligible, reason = False, "no requirements could be extracted from the description"
        elif result["overall_score"] < threshold:
            eligible, reason = False, f"score {result['overall_score']:.1f} below threshold {threshold:g}"
        else:
            eligible, reason = True, ""

        scored.append({"job": enriched, **result, "eligible": eligible, "reason": reason})

    scored.sort(key=lambda s: s["overall_score"], reverse=True)
    queue = [s for s in scored if s["eligible"]][: state.get("max_jobs") or _DEFAULT_MAX_JOBS]
    return {**state, "scored_jobs": scored, "apply_queue": queue, "matching_done": True}
