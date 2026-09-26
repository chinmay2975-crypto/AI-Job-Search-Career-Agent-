from agents.state import AutonomousState
from services.ats_classifier import classify, execution_strategy
from services.ats_discovery import matches_location
from services.job_requirements import extract_requirements
from services.matching import score_match
from services.safety_rails import match_score_threshold

_DEFAULT_MAX_JOBS = 20
# Job-board postings often only have a search snippet; when no skills can be extracted they get
# neutral skills credit instead of the full credit score_match would otherwise give.
_UNKNOWN_SKILLS_CREDIT = 0.5


def run(state: AutonomousState) -> AutonomousState:
    candidate = state.get("candidate", {})
    location = state.get("location", "")
    threshold = match_score_threshold()

    scored = []
    for job in state.get("discovered_jobs", []):
        strategy = execution_strategy(classify(job.get("url", "")))
        requirements = extract_requirements(job, candidate.get("skills", []))
        enriched = {**job, **requirements, "execution_strategy": strategy}

        # Discovery already filtered on location; score it as a match (or neutral for remote roles)
        # rather than letting score_match compare "Pune" to "India - Bengaluru; India - Pune" literally.
        scoring_job = {**enriched, "location": location if matches_location(job, location) else ""}
        result = score_match(
            {**candidate, "location": location}, scoring_job,
            unknown_skills_credit=None if strategy == "auto_submit" else _UNKNOWN_SKILLS_CREDIT,
        )

        if strategy == "auto_submit" and not requirements["required_skills"]:
            # Never auto-apply on a score that couldn't see the job's requirements.
            eligible, reason = False, "no requirements could be extracted from the description"
        elif result["overall_score"] < threshold:
            eligible, reason = False, f"score {result['overall_score']:.1f} below threshold {threshold:g}"
        else:
            eligible = True
            reason = "" if requirements["required_skills"] else "scored from a short summary"

        scored.append({"job": enriched, **result, "eligible": eligible, "reason": reason})

    scored.sort(key=lambda s: s["overall_score"], reverse=True)
    eligible = [s for s in scored if s["eligible"]]
    # --max-jobs caps what the agent submits; drafts and links for you to apply yourself aren't capped.
    auto = [s for s in eligible if s["job"]["execution_strategy"] == "auto_submit"][: state.get("max_jobs") or _DEFAULT_MAX_JOBS]
    manual = [s for s in eligible if s["job"]["execution_strategy"] != "auto_submit"]
    return {**state, "scored_jobs": scored, "apply_queue": auto + manual, "matching_done": True}
