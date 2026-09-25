from unittest.mock import AsyncMock, patch

import pytest

from db.sqlite_repository import SQLiteRepository
from services import ats_classifier
from services.safety_rails import check_idempotent


@pytest.fixture
def repo():
    return SQLiteRepository(db_path=":memory:")


# --- ATS classification -----------------------------------------------------

@pytest.mark.parametrize(
    "url,expected_ats,expected_strategy",
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse", "auto_submit"),
        ("https://jobs.lever.co/acme/456", "lever", "auto_submit"),
        ("https://acme.myworkdayjobs.com/careers/job/789", "workday", "assisted_draft"),
        ("https://www.indeed.com/viewjob?jk=abc", "indeed", "assisted_draft"),
        ("https://www.linkedin.com/jobs/view/123", "linkedin", "blocked"),
        ("https://synthetic-jobs.local/postings/backend-engineer", "synthetic", "auto_submit"),
        ("https://random-startup.example.com/careers/eng", "unknown", "blocked"),
    ],
)
def test_ats_classification(url, expected_ats, expected_strategy):
    ats_type = ats_classifier.classify(url)
    assert ats_type == expected_ats
    assert ats_classifier.execution_strategy(ats_type) == expected_strategy


# --- classify_ats_agent: score gate + LinkedIn hard-stop --------------------

def test_classify_ats_agent_blocks_linkedin(repo):
    from agents import classify_ats_agent

    with patch("agents.classify_ats_agent.get_repository", return_value=repo):
        application = repo.create_application(
            {"job_id": "job-1", "candidate_id": "c1", "match_score": 90, "status": "draft"}
        )
        state = {
            "application_id": application["id"],
            "candidate_id": "c1",
            "job": {"link": "https://www.linkedin.com/jobs/view/1"},
            "match_score": 90,
        }
        result = classify_ats_agent.run(state)

    assert result["execution_strategy"] == "blocked"
    assert repo.get_application(application["id"])["status"] == "blocked"


def test_classify_ats_agent_blocks_below_threshold(repo, monkeypatch):
    monkeypatch.setenv("MATCH_SCORE_THRESHOLD", "70")
    from agents import classify_ats_agent

    with patch("agents.classify_ats_agent.get_repository", return_value=repo):
        application = repo.create_application(
            {"job_id": "job-2", "candidate_id": "c1", "match_score": 40, "status": "draft"}
        )
        state = {
            "application_id": application["id"],
            "candidate_id": "c1",
            "job": {"link": "https://boards.greenhouse.io/acme/jobs/1"},
            "match_score": 40,
        }
        result = classify_ats_agent.run(state)

    assert result["below_threshold"] is True
    assert repo.get_application(application["id"])["status"] == "blocked"


# --- apply_executor_agent: assisted_draft never touches Playwright ---------

@pytest.mark.asyncio
async def test_apply_executor_never_calls_playwright_for_assisted_draft(repo):
    from agents import apply_executor_agent

    application = repo.create_application(
        {"job_id": "job-3", "candidate_id": "c1", "match_score": 90, "status": "pending_approval"}
    )
    state = {
        "application_id": application["id"],
        "candidate_id": "c1",
        "ats_type": "workday",
        "execution_strategy": "assisted_draft",
        "job": {"link": "https://acme.myworkdayjobs.com/job/1"},
        "cover_letter_text": "Dear hiring manager...",
    }

    with patch("agents.apply_executor_agent.get_repository", return_value=repo), patch(
        "agents.apply_executor_agent.playwright_apply.submit_greenhouse_application"
    ) as gh, patch("agents.apply_executor_agent.playwright_apply.submit_lever_application") as lv:
        result = await apply_executor_agent.run(state)

    gh.assert_not_called()
    lv.assert_not_called()
    assert result["status"] == "approved"
    assert repo.get_application(application["id"])["status"] == "approved"


@pytest.mark.asyncio
async def test_apply_executor_respects_dry_run(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from agents import apply_executor_agent

    application = repo.create_application(
        {"job_id": "job-4", "candidate_id": "c1", "match_score": 90, "status": "draft"}
    )
    state = {
        "application_id": application["id"],
        "candidate_id": "c1",
        "ats_type": "synthetic",
        "execution_strategy": "auto_submit",
        "job": {"link": "https://synthetic-jobs.local/postings/x"},
        "cover_letter_text": "Dear hiring manager...",
    }

    with patch("agents.apply_executor_agent.get_repository", return_value=repo), patch(
        "agents.apply_executor_agent.playwright_apply.submit_synthetic_application", new_callable=AsyncMock
    ) as submit_fn:
        submit_fn.return_value = {"status": "dry_run", "detail": "form filled", "application_url": "", "screenshots": []}
        result = await apply_executor_agent.run(state)

    # DRY_RUN still exercises the submitter (so real forms get filled and screenshotted),
    # but always with submit=False - the flag is what keeps Submit from being clicked.
    ctx = submit_fn.call_args.args[3]
    assert ctx["submit"] is False
    assert result["status"] == "dry_run"
    assert repo.get_application(application["id"])["status"] == "dry_run"


@pytest.mark.asyncio
async def test_synthetic_submitter_sends_nothing_without_submit_flag():
    from services import playwright_apply

    with patch("services.playwright_apply.httpx.AsyncClient") as client_cls:
        result = await playwright_apply.submit_synthetic_application(
            {"link": "https://synthetic-jobs.local/postings/x"}, {"candidate_id": "c1"}, "letter", {"submit": False}
        )
        no_ctx_result = await playwright_apply.submit_synthetic_application(
            {"link": "https://synthetic-jobs.local/postings/x"}, {"candidate_id": "c1"}, "letter"
        )

    client_cls.assert_not_called()
    assert result["status"] == "dry_run"
    assert no_ctx_result["status"] == "dry_run"


@pytest.mark.asyncio
async def test_apply_executor_calls_synthetic_submit_when_not_dry_run(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    from agents import apply_executor_agent

    application = repo.create_application(
        {"job_id": "job-5", "candidate_id": "c1", "match_score": 90, "status": "draft"}
    )
    state = {
        "application_id": application["id"],
        "candidate_id": "c1",
        "ats_type": "synthetic",
        "execution_strategy": "auto_submit",
        "job": {"link": "https://synthetic-jobs.local/postings/x"},
        "candidate": {"candidate_id": "c1"},
        "cover_letter_text": "Dear hiring manager...",
    }

    with patch("agents.apply_executor_agent.get_repository", return_value=repo), patch(
        "agents.apply_executor_agent.playwright_apply.submit_synthetic_application", new_callable=AsyncMock
    ) as submit_fn:
        submit_fn.return_value = {
            "status": "submitted", "application_url": "https://synthetic-jobs.local/confirm", "detail": "", "screenshots": [],
        }
        result = await apply_executor_agent.run(state)

    submit_fn.assert_called_once()
    assert submit_fn.call_args.args[3]["submit"] is True
    assert result["status"] == "submitted"
    assert repo.get_application(application["id"])["status"] == "submitted"


@pytest.mark.asyncio
async def test_apply_executor_records_unconfirmed_and_blocks_retry(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    from agents import apply_executor_agent

    application = repo.create_application({"job_id": "job-9", "candidate_id": "c1", "status": "draft"})
    state = {
        "application_id": application["id"], "candidate_id": "c1", "ats_type": "greenhouse",
        "execution_strategy": "auto_submit", "job": {"link": "https://job-boards.greenhouse.io/acme/jobs/1"},
    }
    with patch("agents.apply_executor_agent.get_repository", return_value=repo), patch(
        "agents.apply_executor_agent.playwright_apply.submit_greenhouse_application", new_callable=AsyncMock
    ) as submit_fn:
        submit_fn.return_value = {"status": "unconfirmed", "detail": "no confirmation", "application_url": "", "screenshots": []}
        await apply_executor_agent.run(state)

    assert repo.get_application(application["id"])["status"] == "unconfirmed"
    # Submit may have gone through - never auto-retry it.
    assert check_idempotent(repo, "job-9", "c1") is False


@pytest.mark.asyncio
async def test_apply_executor_has_no_daily_limit(repo, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "false")
    from datetime import datetime, timezone

    from agents import apply_executor_agent

    for i in range(10):  # plenty of real submissions already today
        earlier = repo.create_application({"job_id": f"old-{i}", "candidate_id": "c1", "status": "draft"})
        repo.update_application(earlier["id"], {
            "status": "submitted", "execution_strategy": "auto_submit",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        })

    application = repo.create_application({"job_id": "job-6", "candidate_id": "c1", "status": "draft"})
    state = {
        "application_id": application["id"], "candidate_id": "c1", "ats_type": "synthetic",
        "execution_strategy": "auto_submit", "job": {"link": "https://synthetic-jobs.local/postings/x"},
    }
    with patch("agents.apply_executor_agent.get_repository", return_value=repo), patch(
        "agents.apply_executor_agent.playwright_apply.submit_synthetic_application", new_callable=AsyncMock
    ) as submit_fn:
        submit_fn.return_value = {"status": "submitted", "application_url": "u", "detail": "", "screenshots": []}
        result = await apply_executor_agent.run(state)

    submit_fn.assert_called_once()
    assert result["status"] == "submitted"


# --- safety_rails ------------------------------------------------------------

def test_check_idempotent_blocks_duplicate_non_failed_application(repo):
    repo.create_application({"job_id": "job-7", "candidate_id": "c1", "status": "submitted"})
    assert check_idempotent(repo, "job-7", "c1") is False


@pytest.mark.parametrize("status", ["failed", "dry_run", "needs_manual"])
def test_check_idempotent_allows_retry_when_nothing_was_sent(repo, status):
    repo.create_application({"job_id": "job-8", "candidate_id": "c1", "status": status})
    assert check_idempotent(repo, "job-8", "c1") is True
