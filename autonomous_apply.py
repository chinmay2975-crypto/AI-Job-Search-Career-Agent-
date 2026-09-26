"""Autonomous apply: discover ATS postings for a location, score them against your resume, apply.

Usage:
  python -m autonomous_apply --location "Pune" --resume C:\\path\\to\\resume.pdf
      [--query "python developer"] [--platforms greenhouse,lever] [--max-jobs 20] [--include-remote]
      [--candidate-id ID] [--profile candidate_profile.yaml]

DRY_RUN (in .env) decides whether Submit is clicked. Every attempt is logged to
logs/applications_audit.csv and the applications table (visible in the Streamlit Applications tab).
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents.autonomous_graph import build_autonomous_graph, recursion_limit  # noqa: E402
from db import get_repository  # noqa: E402
from db.sqlite_repository import resolve_db_path  # noqa: E402
from services.ats_discovery import SUPPORTED_PLATFORMS  # noqa: E402
from services.audit_log import AUDIT_LOG_PATH  # noqa: E402
from services.candidate_profile import load_profile, missing_required_fields  # noqa: E402
from services.safety_rails import is_dry_run, match_score_threshold  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover, score, and apply to ATS job postings.")
    parser.add_argument("--location", required=True, help='Target location, e.g. "Pune"')
    parser.add_argument("--resume", required=True, help="Path to your resume PDF")
    parser.add_argument("--query", default="", help="Role keywords; default: profile current_title or top resume skills")
    parser.add_argument("--platforms", default=",".join(SUPPORTED_PLATFORMS),
                        help="Comma-separated sources (default: all): " + ",".join(SUPPORTED_PLATFORMS))
    parser.add_argument("--max-jobs", type=int, default=20, help="Max applications this run")
    parser.add_argument("--include-remote", action="store_true", help="Also include remote roles")
    parser.add_argument("--job-type", choices=["internship", "any"], default=None,
                        help="internship = intern roles only; default: job_type in candidate_profile.yaml")
    parser.add_argument("--candidate-id", default="", help="Defaults to the profile email")
    parser.add_argument("--profile", default=None, help="Path to candidate_profile.yaml")
    return parser.parse_args()


def _print_summary(state: dict) -> None:
    discovered = state.get("discovered_jobs") or []
    scored = state.get("scored_jobs") or []
    results = state.get("results") or []

    print(f"\nDiscovered {len(discovered)} open postings for query {state.get('query')!r} in {state.get('location')}")
    for error in state.get("discovery_errors") or []:
        print(f"  discovery error: {error}")

    if discovered:
        per_site = Counter(j.get("platform", "?") for j in discovered)
        print("  by source: " + ", ".join(f"{k} {v}" for k, v in per_site.most_common()))

    if scored:
        print("\nScores:")
        for s in scored:
            job = s["job"]
            flag = "KEEP " if s["eligible"] else "skip "
            print(f"  [{flag}] {s['overall_score']:5.1f}  {job.get('platform', '')[:11]:11}  {job['company'][:20]:20}  "
                  f"{job['title'][:44]:44}  {s['reason']}")

    if results:
        print("\nResults (apply_yourself = listed for you; pending_approval = drafted for you to send):")
        for r in results:
            print(f"  {r['status']:17} {r['score']:5.1f}  {r.get('platform', '')[:11]:11}  {r['company'][:20]:20}  "
                  f"{r['title'][:38]:38}  {r.get('detail', '')[:100]}")
        counts = Counter(r["status"] for r in results)
        print("\nTotals: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))

    print(f"\nAudit log: {AUDIT_LOG_PATH}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # job titles often contain non-cp1252 characters
    args = _parse_args()

    resume_path = Path(args.resume).expanduser().resolve()
    if not resume_path.exists() or resume_path.suffix.lower() != ".pdf":
        print(f"Resume must be an existing PDF: {resume_path}")
        return 2

    try:
        profile = load_profile(args.profile)
    except FileNotFoundError as e:
        print(e)
        return 2
    missing = missing_required_fields(profile)
    if missing:
        print(f"candidate_profile.yaml is missing required fields: {', '.join(missing)}")
        return 2

    candidate_id = args.candidate_id or profile["email"]
    try:
        get_repository().get_candidate(candidate_id)
    except Exception as e:
        print(f"Can't open the local database {resolve_db_path()} ({type(e).__name__}: {e}).\n"
              "Check the folder is writable, or point SQLITE_DB_PATH in .env somewhere that is.")
        return 2

    mode = "DRY RUN - forms are filled and screenshotted, Submit is NOT clicked" if is_dry_run() else (
        f"LIVE - applications are submitted (up to {args.max_jobs} this run)"
    )
    job_type = args.job_type or str(profile.get("job_type") or "any").strip().lower()
    internship_only = job_type == "internship"
    print(f"Mode: {mode}\nRoles: {'internships only' if internship_only else 'any'}\n"
          f"Match threshold: {match_score_threshold():g}")

    initial_state = {
        "candidate_id": candidate_id,
        "location": args.location,
        "query": args.query,
        "platforms": [p.strip() for p in args.platforms.split(",") if p.strip()],
        "include_remote": args.include_remote,
        "internship_only": internship_only,
        "max_jobs": args.max_jobs,
        "resume_path": str(resume_path),
        "profile": profile,
        "results": [],
    }
    graph = build_autonomous_graph()
    limit = recursion_limit(args.max_jobs, len(initial_state["platforms"]))
    state = asyncio.run(graph.ainvoke(initial_state, config={"recursion_limit": limit}))
    _print_summary(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
