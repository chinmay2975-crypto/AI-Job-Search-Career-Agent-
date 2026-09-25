"""Find open postings directly on ATS platforms (not job-board aggregators).

Discovery = site-restricted web search for posting URLs, then enrichment through each
platform's public job API. The API fetch doubles as a liveness check: stale search
results for closed postings 404 and are dropped.
"""

import html
import re
from urllib.parse import parse_qs, urlparse

import httpx

from services.job_search import serper_search

_GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"
_LEVER_API_HOSTS = {"jobs.lever.co": "https://api.lever.co", "jobs.eu.lever.co": "https://api.eu.lever.co"}

_SITE_FILTERS = {
    "greenhouse": "(site:job-boards.greenhouse.io OR site:boards.greenhouse.io)",
    "lever": "(site:jobs.lever.co OR site:jobs.eu.lever.co)",
    "workday": "site:myworkdayjobs.com",
}

SUPPORTED_PLATFORMS = tuple(_SITE_FILTERS)
DEFAULT_PLATFORMS = ("greenhouse", "lever")

# Board-only search results (a company's whole board) are expanded, capped per board so a
# single large employer can't flood the results.
_MAX_JOBS_PER_BOARD = 5

_LOCATION_ALIASES = {
    "bangalore": "bengaluru",
    "gurgaon": "gurugram",
    "bombay": "mumbai",
    "madras": "chennai",
    "new delhi": "delhi",
}

_http = httpx.Client(timeout=15.0, follow_redirects=True)


# --- URL parsing ---------------------------------------------------------------

def parse_greenhouse_url(url: str) -> tuple[str, str | None] | None:
    """Return (board, job_id) for a Greenhouse board/posting URL; job_id is None for board pages."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in ("job-boards.greenhouse.io", "boards.greenhouse.io"):
        return None

    query = parse_qs(parsed.query)
    parts = [p for p in parsed.path.split("/") if p]

    if parts[:2] == ["embed", "job_app"]:
        board = query.get("for", [None])[0]
        job_id = query.get("token", [None])[0]
        return (board, job_id) if board else None

    if not parts or parts[0] == "embed":
        return None

    board = parts[0]
    if len(parts) >= 3 and parts[1] == "jobs" and parts[2].isdigit():
        return board, parts[2]
    if "gh_jid" in query and query["gh_jid"][0].isdigit():
        return board, query["gh_jid"][0]
    return board, None


def parse_lever_url(url: str) -> tuple[str, str | None, str] | None:
    """Return (company, posting_id, api_base) for a Lever URL; posting_id is None for board pages."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in _LEVER_API_HOSTS:
        return None

    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return None

    company = parts[0]
    posting_id = parts[1] if len(parts) >= 2 and _looks_like_uuid(parts[1]) else None
    return company, posting_id, _LEVER_API_HOSTS[host]


def parse_workday_url(url: str) -> tuple[str, str, str, str] | None:
    """Return (host, tenant, site, job_path) for a Workday posting URL, e.g.
    https://fis.wd5.myworkdayjobs.com/en-GB/SearchJobs/job/Some-Title_JR0308895/apply
    -> ("fis.wd5.myworkdayjobs.com", "fis", "SearchJobs", "job/Some-Title_JR0308895")."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if not host.endswith(".myworkdayjobs.com"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if parts and parts[-1] == "apply":
        parts = parts[:-1]
    if "job" not in parts or parts.index("job") == 0 or parts.index("job") == len(parts) - 1:
        return None
    job_index = parts.index("job")
    return host, host.split(".")[0], parts[job_index - 1], "/".join(parts[job_index:])


def _looks_like_uuid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value.lower()))


# --- Text / location helpers -----------------------------------------------------

def html_to_text(raw: str) -> str:
    """Greenhouse `content` is HTML that is itself HTML-escaped; unescape, strip tags, tidy."""
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _normalize_location(value: str) -> str:
    value = value.lower().strip()
    for alias, canonical in _LOCATION_ALIASES.items():
        value = value.replace(alias, canonical)
    return value


def matches_location(job: dict, location: str, include_remote: bool = False) -> bool:
    if not location:
        return True
    target = _normalize_location(location)
    if any(target in _normalize_location(loc) for loc in job.get("locations", [])):
        return True
    return include_remote and job.get("is_remote", False)


def _matches_keywords(title: str, query: str) -> bool:
    tokens = [t for t in re.findall(r"[a-z0-9+#.]+", query.lower()) if len(t) >= 3]
    return not tokens or any(t in title.lower() for t in tokens)


# --- Greenhouse -------------------------------------------------------------------

def fetch_greenhouse_job(board: str, job_id: str) -> dict | None:
    response = _http.get(f"{_GREENHOUSE_API}/{board}/jobs/{job_id}", params={"questions": "true"})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return normalize_greenhouse_job(board, response.json())


def normalize_greenhouse_job(board: str, data: dict) -> dict:
    job_id = str(data["id"])
    canonical_url = f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    location = (data.get("location") or {}).get("name", "")
    description = html_to_text(data.get("content", ""))

    return {
        "title": data.get("title", ""),
        "company": data.get("company_name") or board,
        "link": canonical_url,
        "url": canonical_url,
        "apply_url": canonical_url,
        "snippet": description[:300],
        "description": description,
        "location": location,
        "locations": [location] if location else [],
        "is_remote": "remote" in location.lower(),
        "platform": "greenhouse",
        "board": board,
        "external_id": job_id,
        "questions": (data.get("questions") or []) + (data.get("location_questions") or []),
        "compliance": data.get("compliance") or [],
        "demographic_questions": data.get("demographic_questions") or {},
        "required_skills": [],
        "min_education_level": "",
        "min_years_experience": 0,
        "source": "ats_discovery",
    }


def _expand_greenhouse_board(board: str, query: str) -> list[dict]:
    response = _http.get(f"{_GREENHOUSE_API}/{board}/jobs")
    if response.status_code == 404:
        return []
    response.raise_for_status()
    listed = [j for j in response.json().get("jobs", []) if _matches_keywords(j.get("title", ""), query)]
    jobs = [fetch_greenhouse_job(board, str(j["id"])) for j in listed[:_MAX_JOBS_PER_BOARD]]
    return [j for j in jobs if j]


# --- Lever ------------------------------------------------------------------------

def fetch_lever_job(company: str, posting_id: str, api_base: str = "https://api.lever.co") -> dict | None:
    response = _http.get(f"{api_base}/v0/postings/{company}/{posting_id}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return normalize_lever_job(company, response.json())


def normalize_lever_job(company: str, data: dict) -> dict:
    categories = data.get("categories") or {}
    location = categories.get("location", "")
    locations = categories.get("allLocations") or ([location] if location else [])
    workplace_type = (data.get("workplaceType") or "").lower()

    sections = [data.get("descriptionPlain", "")]
    for lst in data.get("lists") or []:
        sections.append(lst.get("text", ""))
        sections.append(html_to_text(lst.get("content", "")))
    sections.append(data.get("additionalPlain", ""))
    description = "\n".join(s for s in sections if s).strip()

    hosted_url = data.get("hostedUrl") or f"https://jobs.lever.co/{company}/{data['id']}"

    return {
        "title": data.get("text", ""),
        "company": company.replace("-", " ").title(),
        "link": hosted_url,
        "url": hosted_url,
        "apply_url": data.get("applyUrl") or f"{hosted_url}/apply",
        "snippet": description[:300],
        "description": description,
        "location": location,
        "locations": locations,
        "is_remote": workplace_type == "remote" or any("remote" in loc.lower() for loc in locations),
        "platform": "lever",
        "board": company,
        "external_id": data["id"],
        "questions": [],  # Lever exposes no question schema publicly; the form is inspected at apply time
        "compliance": [],
        "demographic_questions": {},
        "required_skills": [],
        "min_education_level": "",
        "min_years_experience": 0,
        "source": "ats_discovery",
    }


def _expand_lever_board(company: str, api_base: str, query: str) -> list[dict]:
    response = _http.get(f"{api_base}/v0/postings/{company}", params={"mode": "json"})
    if response.status_code == 404:
        return []
    response.raise_for_status()
    listed = [p for p in response.json() if _matches_keywords(p.get("text", ""), query)]
    return [normalize_lever_job(company, p) for p in listed[:_MAX_JOBS_PER_BOARD]]


# --- Workday ----------------------------------------------------------------------
# Discovery only: Workday jobs are drafted for manual submission (assisted_draft), never auto-applied.

def fetch_workday_job(host: str, tenant: str, site: str, job_path: str, search_result: dict | None = None) -> dict | None:
    """Fetch posting details from the tenant's public careers-site API. Some tenants block it
    (403); then fall back to the search result's title/snippet so the job isn't silently lost."""
    response = _http.get(f"https://{host}/wday/cxs/{tenant}/{site}/{job_path}", headers={"Accept": "application/json"})
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        return _workday_job_from_search_result(host, tenant, site, job_path, search_result) if search_result else None
    info = response.json().get("jobPostingInfo") or {}
    if not info or info.get("canApply") is False:
        return None
    return normalize_workday_job(host, tenant, site, job_path, response.json())


def normalize_workday_job(host: str, tenant: str, site: str, job_path: str, data: dict) -> dict:
    info = data.get("jobPostingInfo") or {}
    url = info.get("externalUrl") or f"https://{host}/{site}/{job_path}"
    locations = [loc for loc in [info.get("location", ""), *(info.get("additionalLocations") or [])] if loc]
    description = html_to_text(info.get("jobDescription", ""))
    company = (data.get("hiringOrganization") or {}).get("name") or tenant.replace("-", " ").title()
    return _workday_listing(
        url, info.get("title", ""), company, description, locations, tenant,
        info.get("jobReqId") or job_path.rsplit("_", 1)[-1],
        is_remote="remote" in " ".join(locations).lower() or (info.get("remoteType") or "").lower() == "remote",
    )


def _workday_job_from_search_result(host: str, tenant: str, site: str, job_path: str, result: dict) -> dict:
    snippet = result.get("snippet", "")
    title = re.sub(r"\s*[-|]\s*Workday.*$", "", result.get("title", ""), flags=re.IGNORECASE)
    # Without the API, the snippet is the only place a location can appear.
    return _workday_listing(
        f"https://{host}/{site}/{job_path}", title, tenant.replace("-", " ").title(), snippet,
        [f"{title} {snippet}"], tenant, job_path.rsplit("_", 1)[-1], is_remote="remote" in snippet.lower(),
    )


def _workday_listing(url, title, company, description, locations, tenant, external_id, is_remote) -> dict:
    return {
        "title": title,
        "company": company,
        "link": url,
        "url": url,
        "apply_url": url,
        "snippet": description[:300],
        "description": description,
        "location": locations[0] if locations else "",
        "locations": locations,
        "is_remote": is_remote,
        "platform": "workday",
        "board": tenant,
        "external_id": str(external_id),
        "questions": [],
        "compliance": [],
        "demographic_questions": {},
        "required_skills": [],
        "min_education_level": "",
        "min_years_experience": 0,
        "source": "ats_discovery",
    }


# --- Orchestration ------------------------------------------------------------------

def resolve_url(platform: str, url: str, query: str, search_result: dict | None = None) -> list[dict]:
    """Turn one search-result URL into zero or more normalized, currently-open jobs."""
    if platform == "greenhouse":
        parsed = parse_greenhouse_url(url)
        if not parsed:
            return []
        board, job_id = parsed
        if job_id:
            job = fetch_greenhouse_job(board, job_id)
            return [job] if job else []
        return _expand_greenhouse_board(board, query)

    if platform == "lever":
        parsed = parse_lever_url(url)
        if not parsed:
            return []
        company, posting_id, api_base = parsed
        if posting_id:
            job = fetch_lever_job(company, posting_id, api_base)
            return [job] if job else []
        return _expand_lever_board(company, api_base, query)

    if platform == "workday":
        parsed = parse_workday_url(url)
        if not parsed:
            return []
        job = fetch_workday_job(*parsed, search_result=search_result)
        return [job] if job else []

    return []


def discover_jobs(
    query: str,
    location: str,
    platforms: tuple[str, ...] = DEFAULT_PLATFORMS,
    num_results: int = 10,
    include_remote: bool = False,
) -> dict:
    """Search each platform, resolve results to open jobs, filter by location, dedupe.

    Returns {"jobs": [...], "errors": [...]} - a single bad URL never aborts discovery.
    """
    jobs: list[dict] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()

    for platform in platforms:
        if platform not in _SITE_FILTERS:
            errors.append(f"unsupported platform: {platform}")
            continue

        search_query = f"{_SITE_FILTERS[platform]} {query} {location}".strip()
        try:
            results = serper_search(search_query, num_results)
        except httpx.HTTPError as e:
            errors.append(f"{platform} search failed: {e}")
            continue

        for result in results:
            url = result.get("link", "")
            try:
                resolved = resolve_url(platform, url, query, search_result=result)
            except (httpx.HTTPError, ValueError) as e:  # ValueError: a non-JSON API reply
                errors.append(f"{url}: {e}")
                continue

            for job in resolved:
                key = (job["platform"], job["board"], job["external_id"])
                if key in seen:
                    continue
                seen.add(key)
                if matches_location(job, location, include_remote):
                    jobs.append(job)

    return {"jobs": jobs, "errors": errors}
