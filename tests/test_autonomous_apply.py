import csv
from unittest.mock import AsyncMock, patch

import pytest

from agents import autonomous_matching_agent, planner_agent
from services import audit_log, playwright_apply
from services.ats_discovery import (
    html_to_text,
    matches_location,
    normalize_greenhouse_job,
    normalize_workday_job,
    parse_greenhouse_url,
    parse_lever_url,
    parse_workday_url,
)
from services.form_answers import Question, answer_questions, match_option, questions_from_greenhouse

PROFILE = {
    "first_name": "Jane", "last_name": "Doe", "email": "jane@example.com", "phone": "+91 9800000000",
    "country": "India", "current_location": "Pune", "current_company": "Acme", "current_title": "Engineer",
    "years_experience": 4, "linkedin_url": "", "github_url": "https://github.com/jane", "portfolio_url": "",
    "eeo": {"gender": "decline"},
    "custom_answers": [
        {"match": ["sponsor"], "answer": "No"},
        {"match": ["notice period"], "answer": "30 days"},
        {"match": ["expected ctc"], "answer": ""},  # left blank by the user -> must not be answered
    ],
}
CANDIDATE = {"resume_text": "Built REST APIs with FastAPI and PostgreSQL serving 10k requests/min.", "skills": ["Python"]}
JOB = {"title": "Backend Engineer", "company": "Acme"}
FILES = {"resume": "C:/resume.pdf", "cover_letter": "C:/cl.pdf", "cover_letter_text": "Dear team"}


def _no_llm(*args, **kwargs):
    raise AssertionError("LLM must not be called for this question")


# --- URL parsing ------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://job-boards.greenhouse.io/capco/jobs/8048017", ("capco", "8048017")),
        ("http://boards.greenhouse.io/ethernovia/jobs/5228895007?gh_src=abc", ("ethernovia", "5228895007")),
        ("https://boards.greenhouse.io/embed/job_app?for=stripe&token=8172487", ("stripe", "8172487")),
        ("https://job-boards.greenhouse.io/acme?gh_jid=123", ("acme", "123")),
        ("https://job-boards.greenhouse.io/thoughtworks", ("thoughtworks", None)),
        ("https://stripe.com/jobs/search?gh_jid=8172487", None),
        ("https://www.linkedin.com/jobs/view/1", None),
    ],
)
def test_parse_greenhouse_url(url, expected):
    assert parse_greenhouse_url(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://jobs.lever.co/appzen/71ccd06d-a1ad-4952-b24a-8d60b016ecb5",
         ("appzen", "71ccd06d-a1ad-4952-b24a-8d60b016ecb5", "https://api.lever.co")),
        ("https://jobs.lever.co/appzen/71ccd06d-a1ad-4952-b24a-8d60b016ecb5/apply",
         ("appzen", "71ccd06d-a1ad-4952-b24a-8d60b016ecb5", "https://api.lever.co")),
        ("https://jobs.lever.co/beghouconsulting?commitment=Full-time", ("beghouconsulting", None, "https://api.lever.co")),
        ("https://jobs.eu.lever.co/acme/71ccd06d-a1ad-4952-b24a-8d60b016ecb5",
         ("acme", "71ccd06d-a1ad-4952-b24a-8d60b016ecb5", "https://api.eu.lever.co")),
        ("https://jobs.lever.co/", None),
        ("https://job-boards.greenhouse.io/capco/jobs/1", None),
    ],
)
def test_parse_lever_url(url, expected):
    assert parse_lever_url(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://fis.wd5.myworkdayjobs.com/en-GB/SearchJobs/job/Back-End-Developer_JR0308895/apply",
         ("fis.wd5.myworkdayjobs.com", "fis", "SearchJobs", "job/Back-End-Developer_JR0308895")),
        ("https://fis.wd5.myworkdayjobs.com/searchjobs/job/IND-PUNE-FL7/Back-End_JR0308895",
         ("fis.wd5.myworkdayjobs.com", "fis", "searchjobs", "job/IND-PUNE-FL7/Back-End_JR0308895")),
        ("https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays", None),  # no job path
        ("https://jobs.lever.co/acme/1", None),
    ],
)
def test_parse_workday_url(url, expected):
    assert parse_workday_url(url) == expected


def test_normalize_workday_job_and_classifies_as_assisted_draft():
    from services.ats_classifier import classify, execution_strategy

    job = normalize_workday_job(
        "fis.wd5.myworkdayjobs.com", "fis", "SearchJobs", "job/Back-End_JR0308895",
        {"jobPostingInfo": {"title": "Back End Python", "location": "IND PUNE FL7", "jobDescription": "&lt;p&gt;Python&lt;/p&gt;",
                            "jobReqId": "JR0308895", "externalUrl": "https://fis.wd5.myworkdayjobs.com/SearchJobs/job/Back-End_JR0308895"}},
    )
    assert (job["platform"], job["board"], job["external_id"], job["description"]) == ("workday", "fis", "JR0308895", "Python")
    assert matches_location(job, "Pune")
    assert execution_strategy(classify(job["url"])) == "assisted_draft"  # never auto-submitted


def test_html_to_text_unescapes_greenhouse_content():
    assert html_to_text("&lt;p&gt;Python &amp;amp; SQL&lt;/p&gt;&lt;ul&gt;&lt;li&gt;APIs&lt;/li&gt;&lt;/ul&gt;") == "Python & SQL\n APIs"


def test_normalize_greenhouse_job_uses_canonical_hosted_form():
    job = normalize_greenhouse_job(
        "stripe", {"id": 8172487, "title": "Engineer", "absolute_url": "https://stripe.com/jobs/search?gh_jid=8172487",
                   "location": {"name": "Remote - India"}, "content": "", "company_name": "Stripe"}
    )
    assert job["apply_url"] == "https://job-boards.greenhouse.io/stripe/jobs/8172487"
    assert job["is_remote"] is True


@pytest.mark.parametrize(
    "locations,is_remote,location,include_remote,expected",
    [
        (["India - Bengaluru; India - Pune"], False, "Pune", False, True),
        (["Bangalore, Pune"], False, "Bengaluru", False, True),  # alias
        (["London, United Kingdom"], False, "Pune", False, False),
        (["Remote"], True, "Pune", False, False),
        (["Remote"], True, "Pune", True, True),
    ],
)
def test_matches_location(locations, is_remote, location, include_remote, expected):
    assert matches_location({"locations": locations, "is_remote": is_remote}, location, include_remote) is expected


# --- planner ------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "state,expected",
    [
        ({}, "resume"),
        ({"candidate": {"skills": []}}, "discovery"),
        ({"candidate": {"x": 1}, "discovery_done": True, "discovered_jobs": []}, "end"),
        ({"candidate": {"x": 1}, "discovery_done": True, "discovered_jobs": [{}]}, "matching"),
        ({"candidate": {"x": 1}, "discovery_done": True, "discovered_jobs": [{}], "matching_done": True,
          "apply_queue": [{}]}, "apply"),
        ({"candidate": {"x": 1}, "discovery_done": True, "discovered_jobs": [{}], "matching_done": True,
          "apply_queue": [{}], "cap_reached": True}, "end"),
        ({"candidate": {"x": 1}, "discovery_done": True, "discovered_jobs": [{}], "matching_done": True,
          "apply_queue": []}, "end"),
    ],
)
def test_planner_routing(state, expected):
    assert planner_agent.next_step(state) == expected


# --- answer mapping -------------------------------------------------------------------------

def test_match_option_prefers_exact_then_word_prefix():
    assert match_option("India", ["British Indian Ocean Territory +246", "India +91"]) == "India +91"
    assert match_option("Pune", ["Punewadi, Maharashtra", "Pune, Maharashtra, India"]) == "Pune, Maharashtra, India"
    assert match_option("Not applicable", ["No", "Yes"]) is None


def test_standard_fields_come_from_profile_and_files():
    questions = [Question("first_name", "First Name", "text", True), Question("phone", "Phone", "text", True),
                 Question("resume", "Resume/CV", "file", True), Question("name", "Full name", "text", True)]
    answers, unanswered = answer_questions(questions, PROFILE, CANDIDATE, JOB, FILES)
    values = {a.question.key: a.value for a in answers}
    assert values == {"first_name": "Jane", "phone": "+91 9800000000", "resume": "C:/resume.pdf", "name": "Jane Doe"}
    assert unanswered == []


def test_empty_profile_url_is_unanswered_not_llm_guessed():
    questions = [Question("question_1", "LinkedIn Profile Link", "text", True)]
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions(questions, PROFILE, CANDIDATE, JOB, FILES)
    assert answers == [] and unanswered == questions


def test_personal_questions_use_custom_answers_or_stay_unanswered():
    questions = [
        Question("q1", "Will you require us to sponsor you for a work permit?", "select", True, ["Yes", "No"]),
        Question("q2", "What is your official notice period?", "text", True),
        Question("q3", "What is your Expected CTC in terms of INR?", "text", True),
        Question("q4", "Are you ready to work in UK shift timings?", "select", True, ["Yes", "No"]),
    ]
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions(questions, PROFILE, CANDIDATE, JOB, FILES)
    assert {a.question.key: a.value for a in answers} == {"q1": "No", "q2": "30 days"}
    assert [q.key for q in unanswered] == ["q3", "q4"]


def test_eeo_declines_when_possible_and_is_unanswered_otherwise():
    with_decline = Question("gender", "Gender", "select", False, ["Decline To Self Identify", "Female", "Male"], eeo=True)
    without_decline = Question("q9", "Gender", "select", True, ["Male", "Female", "Others"])
    answers, unanswered = answer_questions([with_decline, without_decline], PROFILE, CANDIDATE, JOB, FILES)
    assert [a.value for a in answers] == ["Decline To Self Identify"]
    assert unanswered == [without_decline]


def test_country_of_residence_and_overall_experience_are_deterministic():
    questions = [
        Question("q1", "Please select the country where you currently reside.", "select", True, ["Ireland", "India"]),
        Question("q2", "How many years of experience do you have in overall?", "select", True,
                 ["0-2yrs", "2-6yrs", "6-7yrs", "7-10yrs"]),
    ]
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions(questions, PROFILE, CANDIDATE, JOB, FILES)
    assert {a.question.key: a.value for a in answers} == {"q1": "India", "q2": "2-6yrs"}
    assert unanswered == []


def test_llm_dropdown_answer_needs_evidence_found_in_resume():
    questions = [
        Question("q1", "Do you have experience building backend applications?", "select", True, ["Yes", "No"]),
        Question("q2", "Have you built CI/CD pipelines?", "select", True, ["Yes", "No"]),
    ]
    llm_reply = """```json
    [{"key": "q1", "status": "answered", "answer": "Yes", "evidence": "Built REST APIs with FastAPI and PostgreSQL"},
     {"key": "q2", "status": "answered", "answer": "Yes", "evidence": "Designed Jenkins pipelines for 20 teams"}]
    ```"""
    with patch("services.form_answers.complete", return_value=llm_reply):
        answers, unanswered = answer_questions(questions, PROFILE, CANDIDATE, JOB, FILES)
    assert {a.question.key: a.value for a in answers} == {"q1": "Yes"}
    assert [q.key for q in unanswered] == ["q2"]  # its "evidence" isn't in the resume


def test_optional_unknown_questions_are_left_blank_without_llm():
    questions = [Question("q1", "Anything else you'd like to share?", "textarea", False)]
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions(questions, PROFILE, CANDIDATE, JOB, FILES)
    assert answers == [] and unanswered == []


def test_questions_from_greenhouse_schema():
    job = {
        "questions": [
            {"label": "Resume/CV", "required": True,
             "fields": [{"name": "resume", "type": "input_file"}, {"name": "resume_text", "type": "textarea"}]},
            {"label": "Countries", "required": True,
             "fields": [{"name": "question_7[]", "type": "multi_value_multi_select",
                         "values": [{"label": "India", "value": 1}]}]},
        ],
        "compliance": [{"type": "eeoc", "questions": [
            {"label": "Gender", "required": False,
             "fields": [{"name": "gender", "type": "multi_value_single_select", "values": [{"label": "Male", "value": 1}]}]}
        ]}],
    }
    questions = questions_from_greenhouse(job)
    assert [(q.key, q.kind, q.eeo) for q in questions] == [
        ("resume", "file", False), ("question_7", "multiselect", False), ("gender", "select", True)
    ]


# --- matching -------------------------------------------------------------------------------

def test_matching_marks_jobs_without_extracted_skills_ineligible(monkeypatch):
    monkeypatch.setenv("MATCH_SCORE_THRESHOLD", "0")
    jobs = [{"title": "A", "description": "x", "locations": ["Pune"]},
            {"title": "B", "description": "python", "locations": ["Pune"]}]
    reqs = [{"required_skills": [], "min_years_experience": 0, "min_education_level": ""},
            {"required_skills": ["Python"], "min_years_experience": 0, "min_education_level": ""}]
    state = {"candidate": {"skills": ["Python"], "education_level": "bachelor", "years_experience": 3},
             "location": "Pune", "discovered_jobs": jobs}
    with patch("agents.autonomous_matching_agent.extract_requirements", side_effect=reqs):
        result = autonomous_matching_agent.run(state)

    by_title = {s["job"]["title"]: s for s in result["scored_jobs"]}
    assert by_title["A"]["eligible"] is False and "no requirements" in by_title["A"]["reason"]
    assert by_title["B"]["eligible"] is True
    assert [s["job"]["title"] for s in result["apply_queue"]] == ["B"]


# --- submit outcome detection --------------------------------------------------------------------

class _FakePage:
    def __init__(self, body: str, url: str = "https://job-boards.greenhouse.io/acme/jobs/1"):
        self._body, self.url = body, url

    async def inner_text(self, selector):
        return self._body


@pytest.fixture
def fast_outcome(monkeypatch):
    monkeypatch.setattr(playwright_apply, "_OUTCOME_TIMEOUT_S", 1)
    monkeypatch.setattr(playwright_apply.asyncio, "sleep", AsyncMock())


async def test_outcome_confirmation_counts_as_submitted(fast_outcome):
    status, _ = await playwright_apply._wait_for_outcome(_FakePage("Thank you for applying!"), "start")
    assert status == "submitted"


async def test_outcome_challenge_is_needs_manual(fast_outcome):
    with patch.object(playwright_apply, "_challenge_visible", AsyncMock(return_value=True)):
        status, detail = await playwright_apply._wait_for_outcome(_FakePage("Apply for this job"), "start")
    assert status == "needs_manual" and "CAPTCHA" in detail


async def test_outcome_without_confirmation_is_unconfirmed_not_submitted(fast_outcome):
    with patch.object(playwright_apply, "_challenge_visible", AsyncMock(return_value=False)), patch.object(
        playwright_apply, "_visible_validation_errors", AsyncMock(return_value=[])
    ):
        status, _ = await playwright_apply._wait_for_outcome(_FakePage("Apply for this job"), "start")
    assert status == "unconfirmed"


async def test_greenhouse_submitter_stops_before_browser_when_required_question_unanswered():
    job = {"questions": [{"label": "What is your Expected CTC?", "required": True,
                          "fields": [{"name": "question_1", "type": "input_text"}]}]}
    ctx = {"application_id": "t1", "profile": PROFILE, "resume_path": "C:/resume.pdf", "submit": True}
    with patch("services.playwright_apply.async_playwright", side_effect=AssertionError("no browser expected")), patch(
        "services.playwright_apply.write_cover_letter_pdf", return_value="C:/cl.pdf"
    ):
        result = await playwright_apply.submit_greenhouse_application(job, CANDIDATE, "Dear team", ctx)
    assert result["status"] == "needs_manual" and "Expected CTC" in result["detail"]


# --- audit log --------------------------------------------------------------------------------------

def test_audit_log_writes_header_and_rows(tmp_path):
    path = tmp_path / "audit.csv"
    job = {"company": "Acme", "title": "Engineer", "platform": "lever", "url": "https://jobs.lever.co/acme/1"}
    audit_log.record(job, "submitted", 81.5, "app-1", "confirmation detected", path=path)
    audit_log.record(job, "needs_manual", 75.0, "app-2", "unanswered", path=path)

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert [(r["company"], r["role"], r["platform"], r["status"]) for r in rows] == [
        ("Acme", "Engineer", "lever", "submitted"), ("Acme", "Engineer", "lever", "needs_manual")
    ]
    assert all(r["timestamp"] for r in rows)
