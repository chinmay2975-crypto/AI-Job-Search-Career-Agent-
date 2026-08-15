import json

from agents.state import GraphState
from services.llm import complete

_GAP_PROMPT = """Candidate skills: {skills}

Top matched job descriptions:
{job_descriptions}

Based on the job descriptions above, list the skills the candidate is missing or should
strengthen to be a stronger fit. Return strict JSON: a list of short skill strings, no
markdown, no commentary. Example: ["Kubernetes", "GraphQL"]
"""

_TOP_N = 5


def run(state: GraphState) -> GraphState:
    candidate = state.get("candidate", {})
    matches = state.get("matches", [])[:_TOP_N]

    if not matches:
        return {**state, "skill_gaps": []}

    job_descriptions = "\n\n".join(
        f"- {m['job'].get('title', '')}: {m['job'].get('description', '')}" for m in matches
    )
    prompt = _GAP_PROMPT.format(skills=", ".join(candidate.get("skills", [])), job_descriptions=job_descriptions)

    raw = complete(prompt, temperature=0.2)
    try:
        skill_gaps = json.loads(raw)
    except json.JSONDecodeError:
        skill_gaps = []

    return {**state, "skill_gaps": skill_gaps}
