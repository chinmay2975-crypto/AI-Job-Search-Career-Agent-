"""Seed the synthetic sandbox job postings into the jobs table.

Usage: python -m db.seed_synthetic_jobs
"""

from dotenv import load_dotenv

load_dotenv()

from db import get_repository  # noqa: E402
from services.synthetic_jobs import SYNTHETIC_JOBS  # noqa: E402


def main():
    repo = get_repository()
    for job in SYNTHETIC_JOBS:
        created = repo.create_job(job)
        print(f"seeded: {created['title']} -> {created['url']}")


if __name__ == "__main__":
    main()
