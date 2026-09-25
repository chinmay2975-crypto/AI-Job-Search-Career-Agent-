import os

from db.repository import Repository


def is_dry_run() -> bool:
    return os.getenv("DRY_RUN", "true").strip().lower() != "false"


def match_score_threshold() -> float:
    return float(os.getenv("MATCH_SCORE_THRESHOLD", "70"))


def check_idempotent(repo: Repository, job_id: str, candidate_id: str) -> bool:
    """True unless this job/candidate already has an application that may have reached the employer
    (anything outside RETRYABLE_STATUSES: submitted, unconfirmed, awaiting approval, ...)."""
    return repo.get_blocking_application_for_job(job_id, candidate_id) is None
