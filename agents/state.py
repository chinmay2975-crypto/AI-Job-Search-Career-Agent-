from typing import TypedDict


class CandidateProfile(TypedDict, total=False):
    candidate_id: str
    resume_text: str
    skills: list[str]
    education_level: str
    years_experience: float
    projects: list[str]
    location: str


class JobListing(TypedDict, total=False):
    title: str
    company: str
    link: str
    snippet: str
    description: str
    location: str
    required_skills: list[str]
    min_education_level: str
    min_years_experience: float


class MatchResult(TypedDict, total=False):
    job: JobListing
    overall_score: float
    components: dict[str, float]


class GraphState(TypedDict, total=False):
    candidate_id: str
    resume_bytes: bytes
    candidate: CandidateProfile
    search_query: str
    location: str
    jobs: list[JobListing]
    matches: list[MatchResult]
    skill_gaps: list[str]
    error: str
