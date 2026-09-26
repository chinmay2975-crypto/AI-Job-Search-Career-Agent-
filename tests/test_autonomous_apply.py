import csv
from unittest.mock import AsyncMock, patch

import pytest

from agents import autonomous_matching_agent, planner_agent
from services import audit_log, playwright_apply
from services.ats_discovery import (
    discover_jobs,
    html_to_text,
    is_internship,
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


@pytest.mark.parametrize(
    "job,expected",
    [
        ({"title": "Software Engineering Intern"}, True),
        ({"title": "Summer Internship - Data"}, True),
        ({"title": "Interns - Platform (2027)"}, True),
        ({"title": "Associate Engineer", "employment_type": "Intern"}, True),  # Lever commitment
        ({"title": "Internal Tools Engineer"}, False),                        # "Internal" is not an internship
        ({"title": "International Sales Lead"}, False),
        ({"title": "Senior Python Developer", "employment_type": "Full-time"}, False),
    ],
)
def test_is_internship(job, expected):
    assert is_internship(job) is expected


def test_discover_jobs_internship_only_adds_intern_to_search_and_filters(monkeypatch):
    searched = []
    monkeypatch.setattr("services.ats_discovery.serper_search",
                        lambda q, n: searched.append(q) or [{"link": "a"}, {"link": "b"}])
    jobs = {
        "a": {"title": "Backend Intern", "platform": "lever", "board": "x", "external_id": "1", "locations": ["Pune"]},
        "b": {"title": "Backend Engineer", "platform": "lever", "board": "x", "external_id": "2", "locations": ["Pune"]},
    }
    monkeypatch.setattr("services.ats_discovery.resolve_url", lambda platform, url, query, search_result=None: [jobs[url]])

    result = discover_jobs("python developer", "Pune", platforms=("lever",), internship_only=True)

    assert "python developer intern" in searched[0]
    assert [j["title"] for j in result["jobs"]] == ["Backend Intern"]

    searched.clear()
    discover_jobs("Software Development Engineer Intern", "Pune", platforms=("lever",), internship_only=True)
    assert "Intern intern" not in searched[0]  # already asks for interns


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

_NO_REQS = {"required_skills": [], "min_years_experience": 0, "min_education_level": ""}
_PY_REQS = {"required_skills": ["Python"], "min_years_experience": 0, "min_education_level": ""}
_MATCH_CANDIDATE = {"skills": ["Python"], "education_level": "bachelor", "years_experience": 3}


def test_matching_never_auto_applies_without_extracted_skills(monkeypatch):
    monkeypatch.setenv("MATCH_SCORE_THRESHOLD", "0")
    jobs = [{"title": "A", "description": "x", "locations": ["Pune"], "url": "https://jobs.lever.co/acme/1"},
            {"title": "B", "description": "python", "locations": ["Pune"], "url": "https://jobs.lever.co/acme/2"}]
    state = {"candidate": _MATCH_CANDIDATE, "location": "Pune", "discovered_jobs": jobs}
    with patch("agents.autonomous_matching_agent.extract_requirements", side_effect=[_NO_REQS, _PY_REQS]):
        result = autonomous_matching_agent.run(state)

    by_title = {s["job"]["title"]: s for s in result["scored_jobs"]}
    assert by_title["A"]["eligible"] is False and "no requirements" in by_title["A"]["reason"]
    assert by_title["B"]["eligible"] is True
    assert [s["job"]["title"] for s in result["apply_queue"]] == ["B"]


def test_matching_gives_board_leads_neutral_not_full_skills_credit(monkeypatch):
    monkeypatch.setenv("MATCH_SCORE_THRESHOLD", "0")
    lead = {"title": "Python Intern", "description": "short snippet", "locations": ["Pune"],
            "url": "https://in.linkedin.com/jobs/view/python-intern-4247033283"}
    state = {"candidate": _MATCH_CANDIDATE, "location": "Pune", "discovered_jobs": [lead]}
    with patch("agents.autonomous_matching_agent.extract_requirements", return_value=_NO_REQS):
        result = autonomous_matching_agent.run(state)

    scored = result["scored_jobs"][0]
    assert scored["components"]["skills"] == 0.5          # not the 1.0 an empty requirement list would give
    assert scored["eligible"] is True and scored["reason"] == "scored from a short summary"
    assert result["apply_queue"][0]["job"]["execution_strategy"] == "link_only"


def test_max_jobs_caps_only_auto_submissions(monkeypatch):
    monkeypatch.setenv("MATCH_SCORE_THRESHOLD", "0")
    auto = [{"title": f"A{i}", "description": "python", "locations": ["Pune"], "url": f"https://jobs.lever.co/x/{i}"}
            for i in range(3)]
    leads = [{"title": f"L{i}", "description": "python", "locations": ["Pune"],
              "url": f"https://www.naukri.com/job-listings-python-intern-{i}"} for i in range(3)]
    state = {"candidate": _MATCH_CANDIDATE, "location": "Pune", "discovered_jobs": auto + leads, "max_jobs": 1}
    with patch("agents.autonomous_matching_agent.extract_requirements", return_value=_PY_REQS):
        result = autonomous_matching_agent.run(state)
    strategies = [s["job"]["execution_strategy"] for s in result["apply_queue"]]
    assert strategies.count("auto_submit") == 1 and strategies.count("link_only") == 3


# --- job boards ---------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "platform,raw,expected",
    [
        ("internshala", "Python Development Internship in Pune at Hosting Duty",
         {"title": "Python Development Internship", "company": "Hosting Duty", "location": "Pune"}),
        ("internshala", "Python Development part time job/internship at Pune in Skillbit",
         {"title": "Python Development part time job/internship", "company": "Skillbit", "location": "Pune"}),
        ("linkedin", "Sarvify Solutions hiring Python Developer Intern in Pune ...",
         {"title": "Python Developer Intern", "company": "Sarvify Solutions", "location": "Pune"}),
        ("linkedin", "Python Developer Intern at ProDT Consulting Services",
         {"title": "Python Developer Intern", "company": "ProDT Consulting Services", "location": ""}),
        ("naukri", "Python Developer Intern - Pune - Techlift Technologies",
         {"title": "Python Developer Intern", "company": "Techlift Technologies", "location": "Pune"}),
        ("unstop", "Python Internship in Pune at Happieloop Technologies",
         {"title": "Python Internship", "company": "Happieloop Technologies", "location": "Pune"}),
        ("wellfound", "Backend Engineer Intern / Fresher – Python",
         {"title": "Backend Engineer Intern / Fresher – Python", "company": "", "location": ""}),
    ],
)
def test_board_title_parsing(platform, raw, expected):
    from services.board_sources import parse_title
    assert parse_title(platform, raw) == expected


@pytest.mark.parametrize(
    "platform,url,is_posting",
    [
        ("indeed", "https://in.indeed.com/q-python-internship-l-pune,-maharashtra-jobs.html", False),
        ("indeed", "https://in.indeed.com/viewjob?jk=abc123", True),
        ("glassdoor", "https://www.glassdoor.co.in/Job/pune-python-internship-jobs-SRCH_IL.0,4.htm", False),
        ("foundit", "https://www.foundit.in/search/python-internship-jobs-in-pune", False),
        ("unstop", "https://unstop.com/internships/python-internship-happieloop-technologies-1548234", True),
        ("linkedin", "https://in.linkedin.com/jobs/view/python-intern-4247033283", True),
    ],
)
def test_board_listing_pages_are_skipped(platform, url, is_posting):
    from services.board_sources import is_posting_url
    assert is_posting_url(platform, url) is is_posting


def test_linkedin_is_resolved_from_search_result_without_fetching(monkeypatch):
    from services import board_sources

    monkeypatch.setattr(board_sources._http, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    job = board_sources.resolve("linkedin", "https://in.linkedin.com/jobs/view/python-intern-at-x-4247033283",
                                {"title": "X hiring Python Intern in Pune", "snippet": "Python, FastAPI"})
    assert (job["title"], job["company"], job["external_id"]) == ("Python Intern", "X", "4247033283")
    assert matches_location(job, "Pune") and is_internship(job)


@pytest.mark.parametrize(
    "label,clicked",
    [("Decline all", True), ("Reject all", True), ("Only necessary", True), ("Dismiss", True),
     ("Accept all", False), ("Accept", False), ("Accept all cookies", False), ("Allow all", False)],
)
def test_cookie_banner_only_ever_declines(label, clicked):
    assert bool(playwright_apply._COOKIE_DECLINE.match(label)) is clicked


def test_linkedin_truncated_title_recovered_from_url_slug():
    from services import board_sources

    job = board_sources.resolve(
        "linkedin", "https://in.linkedin.com/jobs/view/we%E2%80%99re-hiring-python-intern-at-codebyte-solutions-4461234567",
        {"title": "Codebyte Solutions hiring We're hiring Pytho...", "snippet": "Pune"},
    )
    assert job["title"] == "We’Re Hiring Python Intern" and job["company"] == "Codebyte Solutions"


def test_workable_form_schema_to_questions():
    from services.form_answers import questions_from_workable

    job = {"workable_form": [{"name": "Details", "fields": [
        {"id": "firstname", "label": "First name", "type": "text", "required": True},
        {"id": "resume", "label": "Resume", "type": "file", "required": True},
        {"id": "QA_1", "label": "Preferred Joining Location", "type": "multiple", "required": True,
         "singleOption": False, "options": [{"name": "65", "value": "Gurugram"}, {"name": "66", "value": "Pune"}]},
        {"id": "education", "label": "Education", "type": "group", "required": True},
    ]}]}
    questions = {q.key: q for q in questions_from_workable(job)}
    assert questions["firstname"].kind == "text" and questions["resume"].kind == "file"
    assert (questions["QA_1"].kind, questions["QA_1"].options, questions["QA_1"].option_ids) == (
        "multiselect", ["Gurugram", "Pune"], ["65", "66"])
    assert questions["education"].kind == "unsupported"  # held for the site, never guessed

    answers, unanswered = answer_questions(list(questions.values()), PROFILE, CANDIDATE, JOB,
                                           {**FILES, "resume": "r.pdf"}, overrides={"QA_1": "Pune"})
    assert {a.question.key: a.value for a in answers} == {"firstname": "Jane", "resume": "r.pdf", "QA_1": "Pune"}
    assert [q.key for q in unanswered] == ["education"]


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


async def test_outcome_challenge_is_verification_required(fast_outcome):
    with patch.object(playwright_apply, "_challenge_visible", AsyncMock(return_value=True)):
        status, detail = await playwright_apply._wait_for_outcome(_FakePage("Apply for this job"), "start")
    assert status == "verification_required" and "nothing was submitted" in detail


async def test_outcome_emailed_security_code_is_verification_required(fast_outcome):
    """What Greenhouse showed on the Forma.ai internship after Submit."""
    after = ("Apply for this job ... A verification code was sent to you@example.com. To submit your application, "
             "enter the 8-character code to confirm you're a human. Security code")
    with patch.object(playwright_apply, "_challenge_visible", AsyncMock(return_value=False)):
        status, _ = await playwright_apply._wait_for_outcome(_FakePage(after), "start", before_text="Apply for this job")
    assert status == "verification_required"


async def test_outcome_ignores_confirmation_wording_already_on_the_page(fast_outcome):
    """Greenhouse shows the description on the form page; its wording must not count as a confirmation."""
    page_text = "About us ... Thank you for applying to our team! ... Apply for this job"
    with patch.object(playwright_apply, "_challenge_visible", AsyncMock(return_value=False)), patch.object(
        playwright_apply, "_visible_validation_errors", AsyncMock(return_value=[])
    ):
        status, _ = await playwright_apply._wait_for_outcome(_FakePage(page_text), "start", before_text=page_text)
    assert status == "unconfirmed"


async def test_outcome_without_confirmation_is_unconfirmed_not_submitted(fast_outcome):
    with patch.object(playwright_apply, "_challenge_visible", AsyncMock(return_value=False)), patch.object(
        playwright_apply, "_visible_validation_errors", AsyncMock(return_value=[])
    ):
        status, _ = await playwright_apply._wait_for_outcome(_FakePage("Apply for this job"), "start")
    assert status == "unconfirmed"


class _FormPage:
    """Enough of a Playwright page for the Greenhouse flow to read the form's required fields."""

    def __init__(self, required_fields):
        self.required_fields = required_fields
        self.clicked = False

    async def wait_for_selector(self, *args, **kwargs):
        return None

    async def wait_for_timeout(self, *args):
        return None

    async def evaluate(self, script):
        return self.required_fields


async def _run_greenhouse_with_page(job, page, profile=PROFILE):
    async def fake_run_in_browser(url, ctx, fill_and_submit):
        return await fill_and_submit(page, {"value": False})

    ctx = {"application_id": "t1", "profile": profile, "resume_path": "C:/resume.pdf", "submit": True}
    with patch("services.playwright_apply._run_in_browser", side_effect=fake_run_in_browser), patch(
        "services.playwright_apply.write_cover_letter_pdf", return_value="C:/cl.pdf"
    ):
        return await playwright_apply.submit_greenhouse_application(job, CANDIDATE, "Dear team", ctx)


async def test_greenhouse_submitter_holds_unanswered_schema_question():
    job = {"questions": [{"label": "What is your Expected CTC?", "required": True,
                          "fields": [{"name": "question_1", "type": "input_text"}]}]}
    result = await _run_greenhouse_with_page(job, _FormPage([]))
    assert result["status"] == "needs_manual" and "Expected CTC" in result["detail"]
    assert [q["key"] for q in result["pending_questions"]] == ["question_1"]


async def test_greenhouse_submitter_holds_required_page_fields_missing_from_schema():
    """The Education section isn't in the API schema; without profile education it must be held."""
    page = _FormPage([
        {"id": "first_name", "label": "First Name*", "combobox": False, "filled": False},
        {"id": "school--0", "label": "School*", "combobox": True, "filled": False},
        {"id": "start-year--0", "label": "Start date year*", "combobox": False, "filled": False},
    ])
    job = {"questions": [{"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]}]}
    result = await _run_greenhouse_with_page(job, page)  # PROFILE has no education
    assert result["status"] == "needs_manual"
    assert {q["key"]: q["kind"] for q in result["pending_questions"]} == {"school--0": "select", "start-year--0": "text"}


def test_page_fields_merge_and_education_answers():
    from services.form_answers import questions_from_page_fields

    schema = [Question("location", "Location", "text", True)]
    fields = [
        {"id": "candidate-location", "label": "Location*", "combobox": True},  # rendering of the schema's "location"
        {"id": "country", "label": "Country*", "combobox": True},              # phone country picker
        {"id": "school--0", "label": "School*", "combobox": True},
        {"id": "degree--0", "label": "Degree*", "combobox": True},
        {"id": "end-year--0", "label": "End date year*", "combobox": False},
    ]
    extra = questions_from_page_fields(schema, fields)
    assert [(q.key, q.label, q.kind) for q in extra] == [
        ("school--0", "School", "select"), ("degree--0", "Degree", "select"), ("end-year--0", "End date year", "text"),
    ]

    profile = {**PROFILE, "education": {"school": "MIT World Peace University", "degree": "Bachelor's Degree",
                                        "end_year": 2027}}
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions(extra, profile, CANDIDATE, JOB, FILES)
    assert {a.question.key: a.value for a in answers} == {
        "school--0": "MIT World Peace University", "degree--0": "Bachelor's Degree", "end-year--0": "2027"}
    assert unanswered == []


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
