"""Job boards where the agent only finds and scores postings; you apply on the site yourself.

A posting's title, company, and location come from the web-search result (title, snippet, URL).
Pages are never fetched - except Internshala posting pages, whose robots.txt allows
/internship/detail/, fetched once per posting (politely spaced) for a fuller description.
LinkedIn pages are never opened.
"""

import html
import re
import time
from urllib.parse import unquote, urlparse

import httpx

SITE_FILTERS = {
    "internshala": "site:internshala.com/internship/detail",
    "linkedin": "site:linkedin.com/jobs/view",
    "naukri": "site:naukri.com/job-listings",
    "indeed": "(site:in.indeed.com/viewjob OR site:indeed.com/viewjob)",
    "wellfound": "site:wellfound.com/jobs",
    "unstop": "(site:unstop.com/internships OR site:unstop.com/jobs)",
    "glassdoor": "(site:glassdoor.co.in/job-listing OR site:glassdoor.com/job-listing)",
    "foundit": "site:foundit.in/job",
}

DISPLAY_NAMES = {
    "internshala": "Internshala", "linkedin": "LinkedIn", "naukri": "Naukri", "indeed": "Indeed",
    "wellfound": "Wellfound", "unstop": "Unstop", "glassdoor": "Glassdoor", "foundit": "Foundit",
}

# Individual postings only - search/listing pages in the results are skipped.
_POSTING_URL = {
    "internshala": r"internshala\.com/internship/detail/",
    "linkedin": r"linkedin\.com/jobs/view/",
    "naukri": r"naukri\.com/job-listings-",
    "indeed": r"indeed\.com/(viewjob|rc/clk)",
    "wellfound": r"wellfound\.com/jobs/\d+",
    "unstop": r"unstop\.com/(internships|jobs)/[^/?#]+-\d+",
    "glassdoor": r"glassdoor\.[a-z.]+/job-listing/",
    "foundit": r"foundit\.in/job/",
}

# How each board titles its postings in search results (first pattern that matches wins).
_HIRING = [r"^(?P<company>.+?) hiring (?P<title>.+?) in (?P<location>.+)$", r"^(?P<company>.+?) hiring (?P<title>.+)$"]

_TITLE_PATTERNS = {
    "internshala": [r"^(?P<title>.+?) (?:in|at) (?P<location>.+?) (?:at|in) (?P<company>.+)$",
                    r"^(?P<title>.+?) at (?P<company>.+)$"],  # work-from-home internships state no city
    "unstop": [r"^(?P<title>.+?) in (?P<location>.+?) at (?P<company>.+)$", r"^(?P<title>.+?) at (?P<company>.+)$",
               r"^(?P<title>.+?) - (?P<company>.+)$"],
    "linkedin": [*_HIRING, r"^(?P<title>.+?) at (?P<company>.+)$"],
    "naukri": [r"^(?P<title>.+?) - (?P<location>[^-]+?) - (?P<company>.+?)(?: - \d+ to \d+ years.*)?$"],
    "indeed": [r"^(?P<title>.+?) - (?P<company>.+?) - (?P<location>.+)$", r"^(?P<title>.+?) - (?P<location>[^-]+)$"],
    "wellfound": [r"^(?P<title>.+?) at (?P<company>.+?) • (?P<location>.+)$", r"^(?P<title>.+?) at (?P<company>.+)$"],
    "foundit": [r"^(?P<title>.+?) Job in (?P<location>.+?) at (?P<company>.+)$",
                r"^(?P<title>.+?) with \d+.*? - (?P<company>.+)$", r"^(?P<title>.+?) at (?P<location>[a-z ]+)$"],
    "glassdoor": [r"^(?P<title>.+?) Job in (?P<location>.+)$", *_HIRING,
                  r"^(?P<title>.+?) - (?P<location>[A-Za-z .]+)(?:\(\w+\))?$"],
}

_SUFFIXES = re.compile(r"\s*(\| LinkedIn|- Indeed\.com|\| Indeed|\| Glassdoor|- foundit.*|\| Unstop|\.\.\.|…)\s*$", re.I)

# When the same posting turns up on several sites, keep the one where the agent can do the most.
SOURCE_PRIORITY = [
    "greenhouse", "lever", "ashby", "workable",                         # auto-apply
    "internshala", "smartrecruiters", "workday",                        # drafted for you
    "linkedin", "naukri", "unstop", "wellfound", "indeed", "glassdoor", "foundit",  # listed for you
]

_INTERNSHALA_MIN_INTERVAL_S = 1.5
_last_internshala_fetch = 0.0
_http = httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})


def is_posting_url(platform: str, url: str) -> bool:
    return bool(re.search(_POSTING_URL.get(platform, r"$^"), url, re.IGNORECASE))


def parse_title(platform: str, raw_title: str) -> dict:
    """Split a search-result title into title / company / location (best effort)."""
    cleaned = _SUFFIXES.sub("", raw_title.strip())
    for pattern in _TITLE_PATTERNS.get(platform, []):
        match = re.match(pattern, cleaned)
        if match:
            parts = {k: _SUFFIXES.sub("", v).strip() for k, v in match.groupdict().items() if v}
            return {"title": parts.get("title", cleaned), "company": parts.get("company", ""),
                    "location": parts.get("location", "")}
    return {"title": cleaned, "company": "", "location": ""}


def _external_id(platform: str, url: str) -> str:
    parsed = urlparse(url)
    if platform == "linkedin":  # same posting appears under in./www. hosts; the trailing number is the id
        match = re.search(r"(\d{6,})/?$", parsed.path)
        if match:
            return match.group(1)
    return f"{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/')}"


def _internshala_page(url: str) -> tuple[str, str]:
    """(full title, summary) from the posting page - robots.txt permits /internship/detail/.
    Empty strings on failure; requests are spaced out to stay polite."""
    global _last_internshala_fetch
    wait = _INTERNSHALA_MIN_INTERVAL_S - (time.monotonic() - _last_internshala_fetch)
    if wait > 0:
        time.sleep(wait)
    _last_internshala_fetch = time.monotonic()
    try:
        response = _http.get(url)
        if response.status_code != 200:
            return "", ""
        page = response.text
        title = re.search(r"<title[^>]*>(.*?)</title>", page, re.S)
        summary = re.search(r'<meta name="description" content="([^"]*)"', page)
        return (html.unescape(title.group(1)).strip() if title else "",
                html.unescape(summary.group(1)).strip() if summary else "")
    except httpx.HTTPError:
        return "", ""


def _linkedin_slug(url: str) -> dict:
    """Title/company from a LinkedIn posting URL slug ("python-developer-intern-at-acme-4247033283").
    Parsed from the URL string only - the page itself is never requested."""
    slug = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    slug = re.sub(r"-\d{6,}$", "", slug)
    title, _, company = slug.partition("-at-")
    result = {"title": title.replace("-", " ").title()} if title else {}
    if company:
        result["company"] = company.replace("-", " ").title()
    return result


def dedupe_key(job: dict) -> tuple[str, str] | None:
    """(company, title) normalized, for spotting one posting listed on several sites."""
    company = re.sub(r"\b(pvt|private|ltd|limited|inc|llp|technologies|technology|solutions|software)\b|[^a-z0-9]",
                     "", (job.get("company") or "").casefold())
    title = re.sub(r"[^a-z0-9]", "", (job.get("title") or "").casefold())
    return (company, title) if company and title else None


def resolve(platform: str, url: str, search_result: dict) -> dict | None:
    """A normalized posting from one search result, or None for non-posting URLs."""
    if not is_posting_url(platform, url):
        return None

    raw_title = search_result.get("title", "")
    snippet = (search_result.get("snippet") or "").strip()
    description = snippet
    if platform == "internshala":
        page_title, summary = _internshala_page(url)
        description = summary or snippet
        raw_title = page_title or raw_title  # search titles are often truncated with "..."
    parsed = parse_title(platform, raw_title)
    if platform == "linkedin" and re.search(r"(\.\.\.|…)\s*$", raw_title):
        parsed = {**parsed, **_linkedin_slug(url)}  # Google truncated the title; the URL slug has it in full

    text = f"{raw_title} {snippet} {urlparse(url).path.replace('-', ' ')}"
    is_remote = bool(re.search(r"\b(remote|work from home|wfh)\b", text, re.IGNORECASE))
    # A stated location is authoritative; without one, the snippet/URL text decides - except for remote
    # postings, which only show up when remote roles are asked for (--include-remote).
    if parsed["location"]:
        locations = [parsed["location"]]
    else:
        locations = [] if is_remote else [text]

    return {
        "title": parsed["title"],
        "company": parsed["company"],
        "link": url,
        "url": url,
        "apply_url": url,
        "snippet": description[:300],
        "description": description,
        "location": parsed["location"],
        "locations": locations,
        "is_remote": is_remote,
        "employment_type": "Internship" if platform == "internshala" else "",
        "platform": platform,
        "board": platform,
        "external_id": _external_id(platform, url),
        "questions": [],
        "compliance": [],
        "demographic_questions": {},
        "required_skills": [],
        "min_education_level": "",
        "min_years_experience": 0,
        "source": "board_search",
    }
