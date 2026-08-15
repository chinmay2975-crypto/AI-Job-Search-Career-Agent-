from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

_WEIGHTS = {
    "skills": 0.40,
    "education": 0.15,
    "experience": 0.20,
    "projects": 0.10,
    "location": 0.05,
    "job_requirements": 0.10,
}


def text_similarity(text_a: str, text_b: str) -> float:
    """TF-IDF + cosine similarity between two texts, in [0, 1]."""
    if not text_a.strip() or not text_b.strip():
        return 0.0
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform([text_a, text_b])
    return float(cosine_similarity(matrix[0], matrix[1])[0][0])


def _overlap_ratio(candidate_items: list[str], required_items: list[str]) -> float:
    if not required_items:
        return 1.0
    candidate_set = {item.lower().strip() for item in candidate_items}
    required_set = {item.lower().strip() for item in required_items}
    return len(candidate_set & required_set) / len(required_set)


def score_match(candidate: dict, job: dict) -> dict:
    """Deterministic weighted match score between a candidate profile and a job listing.

    candidate: {skills: [str], education_level: str, years_experience: float,
                projects: [str], location: str}
    job: {required_skills: [str], min_education_level: str, min_years_experience: float,
          location: str, description: str}
    Returns per-component scores (0-1) and an overall weighted score (0-100).
    """
    skills_score = _overlap_ratio(candidate.get("skills", []), job.get("required_skills", []))

    education_score = 1.0 if _education_meets(
        candidate.get("education_level", ""), job.get("min_education_level", "")
    ) else 0.5

    candidate_years = candidate.get("years_experience", 0) or 0
    required_years = job.get("min_years_experience", 0) or 0
    experience_score = 1.0 if required_years == 0 else min(candidate_years / required_years, 1.0)

    projects_text = " ".join(candidate.get("projects", []))
    projects_score = text_similarity(projects_text, job.get("description", ""))

    candidate_location = candidate.get("location", "").strip().lower()
    job_location = job.get("location", "").strip().lower()
    if not candidate_location or not job_location:
        location_score = 0.5  # unknown location on either side: neither a match nor a mismatch
    else:
        location_score = 1.0 if candidate_location == job_location else 0.0

    candidate_skills_text = " ".join(candidate.get("skills", []))
    job_requirements_score = text_similarity(candidate_skills_text, job.get("description", ""))

    components = {
        "skills": skills_score,
        "education": education_score,
        "experience": experience_score,
        "projects": projects_score,
        "location": location_score,
        "job_requirements": job_requirements_score,
    }
    overall = sum(components[k] * _WEIGHTS[k] for k in _WEIGHTS) * 100

    return {"overall_score": round(overall, 2), "components": components}


_EDUCATION_RANK = {"high_school": 0, "associate": 1, "bachelor": 2, "master": 3, "phd": 4}


def _education_meets(candidate_level: str, required_level: str) -> bool:
    if not required_level:
        return True
    candidate_rank = _EDUCATION_RANK.get(candidate_level.lower().strip(), 0)
    required_rank = _EDUCATION_RANK.get(required_level.lower().strip(), 0)
    return candidate_rank >= required_rank
