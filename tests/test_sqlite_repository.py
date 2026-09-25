from pathlib import Path

from db import get_repository
from db.sqlite_repository import DEFAULT_DB_PATH, PROJECT_ROOT, SQLiteRepository, resolve_db_path
from services.safety_rails import check_daily_cap, check_idempotent


def test_default_db_path_is_anchored_to_project_not_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv("SQLITE_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)  # running from another folder must not create a second database
    assert Path(resolve_db_path()) == DEFAULT_DB_PATH
    assert DEFAULT_DB_PATH == PROJECT_ROOT / "data" / "career_agent.db"


def test_sqlite_db_path_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SQLITE_DB_PATH", "custom/agent.db")
    assert Path(resolve_db_path()) == PROJECT_ROOT / "custom" / "agent.db"

    absolute = tmp_path / "elsewhere.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(absolute))
    assert Path(resolve_db_path()) == absolute


def test_get_repository_uses_configured_file_and_persists(monkeypatch, tmp_path):
    db_file = tmp_path / "nested" / "agent.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))

    get_repository().save_candidate("c1", {"skills": ["Python"]})

    assert db_file.exists()  # parent folder created automatically
    assert get_repository().get_candidate("c1") == {"skills": ["Python"]}  # new connection sees it


def test_wal_mode_enabled_for_file_databases(tmp_path):
    repo = SQLiteRepository(str(tmp_path / "agent.db"))
    assert repo._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_dedupe_and_daily_cap_hold_across_separate_connections(tmp_path, monkeypatch):
    """The API server and the CLI each open their own connection; both must see each other's writes."""
    monkeypatch.setenv("AUTO_SUBMIT_DAILY_CAP", "1")
    db_file = str(tmp_path / "agent.db")
    cli_repo, api_repo = SQLiteRepository(db_file), SQLiteRepository(db_file)

    job = cli_repo.create_job({"title": "Engineer", "url": "https://jobs.lever.co/acme/1", "source": "ats_discovery"})
    application = cli_repo.create_application({"job_id": job["id"], "candidate_id": "c1", "status": "draft"})
    cli_repo.update_application(application["id"], {
        "status": "submitted", "execution_strategy": "auto_submit", "submitted_at": "2099-01-01T00:00:00+00:00",
    })

    assert api_repo.get_job_by_url("https://jobs.lever.co/acme/1")["id"] == job["id"]
    assert check_idempotent(api_repo, job["id"], "c1") is False


def test_daily_cap_counts_todays_submissions(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setenv("AUTO_SUBMIT_DAILY_CAP", "1")
    repo = SQLiteRepository(str(tmp_path / "agent.db"))
    application = repo.create_application({"job_id": "j1", "candidate_id": "c1", "status": "draft"})
    assert check_daily_cap(repo, "c1") is True

    repo.update_application(application["id"], {
        "status": "submitted", "execution_strategy": "auto_submit",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    })
    assert repo.count_auto_submits_today("c1") == 1
    assert check_daily_cap(repo, "c1") is False
