import json
from unittest.mock import patch

from agents import job_search_agent, matching_agent, resume_agent, skill_gap_agent
from services.matching import score_match


def test_resume_agent_parses_llm_json():
    fake_json = json.dumps(
        {
            "skills": ["Python", "SQL"],
            "education_level": "bachelor",
            "years_experience": 3,
            "projects": ["Built a data pipeline"],
            "location": "Remote",
        }
    )
    with patch("agents.resume_agent.resume_parser.extract_text", return_value="resume text"), patch(
        "agents.resume_agent.complete", return_value=fake_json
    ):
        state = resume_agent.run({"candidate_id": "c1", "resume_bytes": b"%PDF-fake"})

    assert state["candidate"]["skills"] == ["Python", "SQL"]
    assert state["candidate"]["education_level"] == "bachelor"
    assert state["candidate"]["resume_text"] == "resume text"


def test_resume_agent_falls_back_on_bad_json():
    with patch("agents.resume_agent.resume_parser.extract_text", return_value="resume text"), patch(
        "agents.resume_agent.complete", return_value="not json"
    ):
        state = resume_agent.run({"candidate_id": "c1", "resume_bytes": b"%PDF-fake"})

    assert state["candidate"]["skills"] == []


def test_job_search_agent_uses_candidate_skills_as_query():
    with patch("agents.job_search_agent.search_jobs", return_value=[{"title": "Engineer"}]) as mock_search:
        state = job_search_agent.run(
            {"candidate": {"skills": ["Python", "SQL"], "location": "Remote"}}
        )

    mock_search.assert_called_once()
    assert state["jobs"] == [{"title": "Engineer"}]


def test_matching_agent_sorts_by_score():
    candidate = {"skills": ["Python"], "education_level": "bachelor", "years_experience": 3, "projects": [], "location": ""}
    jobs = [
        {"title": "Low match", "required_skills": ["Rust"], "description": "", "location": ""},
        {"title": "High match", "required_skills": ["Python"], "description": "Python role", "location": ""},
    ]
    state = matching_agent.run({"candidate": candidate, "jobs": jobs})

    assert state["matches"][0]["job"]["title"] == "High match"
    assert state["matches"][0]["overall_score"] >= state["matches"][1]["overall_score"]


def test_skill_gap_agent_returns_empty_without_matches():
    state = skill_gap_agent.run({"candidate": {"skills": []}, "matches": []})
    assert state["skill_gaps"] == []


def test_skill_gap_agent_parses_llm_list():
    with patch("agents.skill_gap_agent.complete", return_value='["Kubernetes", "GraphQL"]'):
        state = skill_gap_agent.run(
            {
                "candidate": {"skills": ["Python"]},
                "matches": [{"job": {"title": "Backend Eng", "description": "Needs Kubernetes"}}],
            }
        )

    assert state["skill_gaps"] == ["Kubernetes", "GraphQL"]


def test_score_match_perfect_skills_overlap():
    candidate = {"skills": ["Python", "SQL"], "education_level": "bachelor", "years_experience": 5, "projects": [], "location": "Remote"}
    job = {"required_skills": ["Python", "SQL"], "min_education_level": "bachelor", "min_years_experience": 2, "location": "Remote", "description": ""}

    result = score_match(candidate, job)

    assert result["components"]["skills"] == 1.0
    assert result["components"]["location"] == 1.0
    assert result["overall_score"] > 0


def test_score_match_unknown_location_is_neutral():
    candidate = {"skills": ["Python"], "education_level": "bachelor", "years_experience": 3, "projects": [], "location": ""}
    job = {"required_skills": ["Python"], "min_education_level": "bachelor", "min_years_experience": 1, "location": "", "description": ""}

    result = score_match(candidate, job)

    assert result["components"]["location"] == 0.5
