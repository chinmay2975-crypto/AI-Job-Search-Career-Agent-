import os

from db.repository import Repository


def is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").strip().lower() != "false"


def daily_auto_submit_cap() -> int:
    return int(os.getenv("AUTO_SUBMIT_DAILY_CAP", "5"))


def match_score_threshold() -> float:
    return float(os.getenv("MATCH_SCORE_THRESHOLD", "70"))


def check_daily_cap(repo: Repository, candidate_id: str) -> bool:
    """True if the candidate is still under today's auto-submit cap."""
    return repo.count_auto_submits_today(candidate_id) < daily_auto_submit_cap()


def check_idempotent(repo: Repository, job_id: str, candidate_id: str) -> bool:
    """True if there is no existing non-failed application for this job/candidate pair."""
    return repo.get_non_failed_application_for_job(job_id, candidate_id) is None
