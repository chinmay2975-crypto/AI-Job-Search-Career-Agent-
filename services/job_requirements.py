"""Extract structured requirements from a job description so score_match has real inputs.

The LLM only extracts; scoring stays deterministic in services/matching.py. Without this,
ATS postings arrive with no required_skills, and _overlap_ratio would hand every job the full
skills credit.
"""

from services.llm import complete, extract_json

_EDUCATION_LEVELS = {"high_school", "associate", "bachelor", "master", "phd"}

_PROMPT = """Extract the hiring requirements from this job description as strict JSON:
{{
  "required_skills": [up to 10 MUST-HAVE technical skills/tools named in the description; exclude nice-to-haves],
  "min_years_experience": number (minimum years of experience required; 0 if not stated),
  "min_education_level": one of "high_school" | "associate" | "bachelor" | "master" | "phd" | "" (empty if not stated)
}}
Only list skills the description actually names. When a skill is the same thing as one in this
list, use the list's spelling (for consistent matching): {candidate_skills}

Job title: {title}
Description:
{description}
"""


def extract_requirements(job: dict, candidate_skills: list[str]) -> dict:
    raw = complete(
        _PROMPT.format(
            candidate_skills=", ".join(candidate_skills) or "(none)",
            title=job.get("title", ""),
            description=(job.get("description") or "")[:10000],
        ),
        temperature=0.0,
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        return {"required_skills": [], "min_years_experience": 0, "min_education_level": ""}

    skills = [str(s).strip() for s in parsed.get("required_skills") or [] if str(s).strip()][:10]

    try:
        years = float(parsed.get("min_years_experience") or 0)
    except (TypeError, ValueError):
        years = 0.0
    years = min(max(years, 0.0), 30.0)

    education = str(parsed.get("min_education_level") or "").strip().lower()
    if education not in _EDUCATION_LEVELS:
        education = ""

    return {"required_skills": skills, "min_years_experience": years, "min_education_level": education}
