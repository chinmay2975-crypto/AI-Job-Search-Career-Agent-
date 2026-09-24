from urllib.parse import urlparse

_DOMAIN_TO_ATS = {
    "greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "jobs.lever.co": "lever",
    "myworkdayjobs.com": "workday",
    "workday.com": "workday",
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
    "synthetic-jobs.local": "synthetic",
}

_EXECUTION_STRATEGY = {
    "greenhouse": "auto_submit",
    "lever": "auto_submit",
    "synthetic": "auto_submit",
    "workday": "assisted_draft",
    "indeed": "assisted_draft",
    "linkedin": "blocked",
    "unknown": "blocked",
}


def classify(url: str) -> str:
    """Classify a job posting URL/domain into an ATS type."""
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    for known_domain, ats_type in _DOMAIN_TO_ATS.items():
        if domain == known_domain or domain.endswith(f".{known_domain}"):
            return ats_type
    return "unknown"


def execution_strategy(ats_type: str) -> str:
    """Map an ATS type to its execution strategy: auto_submit | assisted_draft | blocked."""
    return _EXECUTION_STRATEGY.get(ats_type, "blocked")
