"""Append-only local CSV record of every application attempt, independent of the database."""

import csv
from datetime import datetime, timezone
from pathlib import Path

AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "applications_audit.csv"

_COLUMNS = [
    "timestamp", "company", "role", "platform", "job_url", "match_score", "status", "application_id", "detail",
]


def record(job: dict, status: str, match_score: float | None = None, application_id: str = "",
           detail: str = "", path: Path | None = None) -> None:
    log_path = path or AUDIT_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()

    with log_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "company": job.get("company", ""),
                "role": job.get("title", ""),
                "platform": job.get("platform", ""),
                "job_url": job.get("url") or job.get("link", ""),
                "match_score": "" if match_score is None else match_score,
                "status": status,
                "application_id": application_id,
                "detail": detail,
            }
        )
