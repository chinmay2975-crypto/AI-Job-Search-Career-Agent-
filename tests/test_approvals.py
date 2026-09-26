import sqlite3
from dataclasses import asdict
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from db.sqlite_repository import SQLiteRepository
from services import application_runner
from services.form_answers import Question, answer_questions, normalize_label

PROFILE = {"first_name": "A", "last_name": "B", "email": "a@example.com", "phone": "+91 9000000000",
           "eeo": {"gender": "decline"}, "custom_answers": [{"match": ["notice period"], "answer": "30 days"}]}
CANDIDATE = {"resume_text": "Built REST APIs with FastAPI.", "skills": ["Python"]}
FILES = {"resume": "r.pdf"}
CTC = Question("q_ctc", "What is your Expected CTC?", "text", True)
SHIFT = Question("q_shift", "Are you ready to work UK shift?", "select", True, ["Yes", "No"])


def _no_llm(*args, **kwargs):
    raise AssertionError("LLM must not be called")


# --- answer precedence ---------------------------------------------------------------

def test_user_answers_resolve_held_questions():
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions([CTC, SHIFT], PROFILE, CANDIDATE, {}, FILES,
                                               overrides={"q_ctc": "12 LPA", "q_shift": "no"})
    assert {a.question.key: (a.value, a.source) for a in answers} == {
        "q_ctc": ("12 LPA", "user"), "q_shift": ("No", "user")}
    assert unanswered == []


def test_user_answer_not_matching_options_stays_held():
    answers, unanswered = answer_questions([SHIFT], PROFILE, CANDIDATE, {}, FILES, overrides={"q_shift": "Maybe"})
    assert answers == [] and unanswered == [SHIFT]


def test_saved_answers_fill_gaps_but_never_override_profile_rules():
    notice = Question("q_notice", "What is your notice period?", "text", True)
    gender = Question("gender", "Gender", "select", True, ["Decline To Self Identify", "Male", "Female"], eeo=True)
    saved = {normalize_label(CTC.label): "12 LPA", normalize_label(notice.label): "90 days",
             normalize_label("Gender"): "Male"}
    with patch("services.form_answers.complete", side_effect=_no_llm):
        answers, unanswered = answer_questions([CTC, notice, gender], PROFILE, CANDIDATE, {}, FILES, saved_answers=saved)
    values = {a.question.key: (a.value, a.source) for a in answers}
    assert values["q_ctc"] == ("12 LPA", "saved")                           # gap filled
    assert values["q_notice"] == ("30 days", "custom_answers")              # profile rule wins
    assert values["gender"] == ("Decline To Self Identify", "eeo")          # profile "decline" wins
    assert unanswered == []


def test_saved_answer_used_for_eeo_question_without_decline_option():
    gender = Question("q_g", "Gender", "select", True, ["Male", "Female", "Others"])
    answers, unanswered = answer_questions([gender], PROFILE, CANDIDATE, {}, FILES,
                                           saved_answers={normalize_label("Gender"): "Male"})
    assert [(a.value, a.source) for a in answers] == [("Male", "saved")] and unanswered == []


# --- repository ----------------------------------------------------------------------

def test_existing_database_gets_new_columns(tmp_path):
    db_file = tmp_path / "old.db"
    with sqlite3.connect(db_file) as conn:  # schema as it was before approvals existed
        conn.execute("CREATE TABLE applications (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, candidate_id TEXT NOT NULL,"
                     " match_score REAL, ats_type TEXT, execution_strategy TEXT, status TEXT NOT NULL DEFAULT 'draft',"
                     " cover_letter_text TEXT, thread_id TEXT, submitted_at TEXT, application_url TEXT,"
                     " error_log TEXT, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO applications (id, job_id, candidate_id, status, created_at) "
                     "VALUES ('a1', 'j1', 'c1', 'submitted', '2026-01-01')")

    repo = SQLiteRepository(str(db_file))
    assert repo.get_application("a1")["status"] == "submitted"  # old rows survive
    repo.update_application("a1", {"pending_questions": [{"key": "k"}]})
    assert repo.get_application("a1")["pending_questions"] == [{"key": "k"}]


def test_pending_questions_and_job_snapshot_roundtrip_and_supersede(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "a.db"))
    app = repo.create_application({"job_id": "j1", "candidate_id": "c1", "status": "needs_manual",
                                   "job_snapshot": {"title": "Eng", "questions": []},
                                   "pending_questions": [asdict(CTC)]})
    stored = repo.get_application(app["id"])
    assert stored["job_snapshot"]["title"] == "Eng" and stored["pending_questions"][0]["key"] == "q_ctc"

    repo.supersede_retryable_applications("j1", "c1")
    assert repo.get_application(app["id"])["status"] == "superseded"
    assert repo.get_blocking_application_for_job("j1", "c1") is None  # superseded never blocks


# --- approving -----------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "agent.db"))
    return SQLiteRepository(str(tmp_path / "agent.db"))


def _held_application(repo) -> dict:
    return repo.create_application({
        "job_id": "j1", "candidate_id": "c1", "status": "needs_manual", "ats_type": "greenhouse",
        "execution_strategy": "auto_submit", "cover_letter_text": "Dear team",
        "job_snapshot": {"title": "Eng", "url": "https://job-boards.greenhouse.io/acme/jobs/1", "questions": []},
        "pending_questions": [asdict(CTC), asdict(SHIFT)], "resume_path": "C:/r.pdf",
    })


async def test_answer_and_submit_reruns_with_answers_and_remembers(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    application = _held_application(repo)
    with patch("services.playwright_apply.submit_greenhouse_application", new_callable=AsyncMock) as submit_fn, \
            patch("services.application_runner._default_profile", return_value=PROFILE):
        submit_fn.return_value = {"status": "dry_run", "detail": "filled", "application_url": "", "screenshots": [],
                                  "pending_questions": []}
        result = await application_runner.answer_and_submit(
            application["id"], {"q_ctc": "12 LPA", "q_shift": "No"}, remember=True)

    ctx = submit_fn.call_args.args[3]
    assert ctx["overrides"] == {"q_ctc": "12 LPA", "q_shift": "No"} and ctx["submit"] is False
    assert ctx["resume_path"] == "C:/r.pdf"
    assert result["status"] == "dry_run"
    stored = repo.get_application(application["id"])
    assert stored["status"] == "dry_run" and stored["pending_questions"] is None
    assert repo.get_saved_answers("c1") == {normalize_label(CTC.label): "12 LPA", normalize_label(SHIFT.label): "No"}


async def test_answer_and_submit_without_remember_saves_nothing(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    application = _held_application(repo)
    with patch("services.playwright_apply.submit_greenhouse_application", new_callable=AsyncMock) as submit_fn, \
            patch("services.application_runner._default_profile", return_value=PROFILE):
        submit_fn.return_value = {"status": "dry_run", "detail": "", "application_url": "", "screenshots": []}
        await application_runner.answer_and_submit(application["id"], {"q_ctc": "12 LPA"}, remember=False)
    assert repo.get_saved_answers("c1") == {}


async def test_only_held_applications_can_be_approved(repo):
    submitted = repo.create_application({"job_id": "j2", "candidate_id": "c1", "status": "submitted"})
    with pytest.raises(application_runner.NotAwaitingAnswersError):
        await application_runner.answer_and_submit(submitted["id"], {"q": "x"})


def test_api_config_and_answer_endpoint(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from main import app

    client = TestClient(app)
    config = client.get("/config").json()
    assert config["dry_run"] is True and "default_candidate_id" in config

    application = _held_application(repo)
    listed = client.get("/applications", params={"candidate_id": "c1"}).json()
    assert listed[0]["pending_questions"][0]["label"] == CTC.label and "job_snapshot" not in listed[0]

    with patch("services.playwright_apply.submit_greenhouse_application", new_callable=AsyncMock) as submit_fn, \
            patch("services.application_runner._default_profile", return_value=PROFILE):
        submit_fn.return_value = {"status": "dry_run", "detail": "", "application_url": "", "screenshots": []}
        response = client.post(f"/applications/{application['id']}/answer",
                               json={"answers": {"q_ctc": "12 LPA", "q_shift": "No"}})
    assert response.status_code == 200 and response.json()["status"] == "dry_run"

    again = client.post(f"/applications/{application['id']}/answer", json={"answers": {}})
    assert again.status_code == 409  # no longer held
