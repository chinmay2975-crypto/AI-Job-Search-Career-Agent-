from urllib.parse import urlparse

_DOMAIN_TO_ATS = {
    "greenhouse.io": "greenhouse",
    "boards.greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "jobs.lever.co": "lever",
    "ashbyhq.com": "ashby",
    "workable.com": "workable",
    "smartrecruiters.com": "smartrecruiters",
    "myworkdayjobs.com": "workday",
    "workday.com": "workday",
    "internshala.com": "internshala",
    "linkedin.com": "linkedin",
    "indeed.com": "indeed",
    "naukri.com": "naukri",
    "wellfound.com": "wellfound",
    "unstop.com": "unstop",
    "glassdoor.co.in": "glassdoor",
    "glassdoor.com": "glassdoor",
    "foundit.in": "foundit",
    "synthetic-jobs.local": "synthetic",
}

# auto_submit:    the agent fills and submits the form (DRY_RUN and your approvals still apply)
# assisted_draft: the agent drafts a cover letter; you submit on the site. Used where applying needs
#                 your account (Workday, Internshala) or the apply page is behind bot protection
#                 (SmartRecruiters) - never automated.
# link_only:      the agent lists the posting with a score and link, nothing else; you apply on the
#                 site. LinkedIn postings are never opened, drafted for, or applied to by the agent.
# blocked:        unrecognized sites.
_EXECUTION_STRATEGY = {
    "greenhouse": "auto_submit",
    "lever": "auto_submit",
    "ashby": "auto_submit",
    "workable": "auto_submit",
    "synthetic": "auto_submit",
    "workday": "assisted_draft",
    "internshala": "assisted_draft",
    "smartrecruiters": "assisted_draft",
    "linkedin": "link_only",
    "indeed": "link_only",
    "naukri": "link_only",
    "wellfound": "link_only",
    "unstop": "link_only",
    "glassdoor": "link_only",
    "foundit": "link_only",
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
    """Map an ATS type to its execution strategy: auto_submit | assisted_draft | link_only | blocked."""
    return _EXECUTION_STRATEGY.get(ats_type, "blocked")
