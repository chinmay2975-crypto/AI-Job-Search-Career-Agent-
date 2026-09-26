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
    internship_only: bool
    jobs: list[JobListing]
    matches: list[MatchResult]
    skill_gaps: list[str]
    error: str


class ApplicationState(TypedDict, total=False):
    application_id: str
    candidate_id: str
    candidate: CandidateProfile
    job: JobListing
    match_score: float
    ats_type: str
    execution_strategy: str  # auto_submit | assisted_draft | blocked
    cover_letter_text: str
    human_approved: bool
    below_threshold: bool
    status: str
    resume_path: str
    profile: dict
    submit_detail: str


class ScoredJob(TypedDict, total=False):
    job: JobListing
    overall_score: float
    components: dict[str, float]
    eligible: bool
    reason: str


class AutonomousState(TypedDict, total=False):
    candidate_id: str
    location: str
    query: str
    platforms: list[str]
    include_remote: bool
    internship_only: bool
    max_jobs: int
    resume_path: str
    profile: dict
    candidate: CandidateProfile
    discovered_jobs: list[JobListing]
    discovery_errors: list[str]
    discovery_done: bool
    scored_jobs: list[ScoredJob]
    matching_done: bool
    apply_queue: list[ScoredJob]
    results: list[dict]
