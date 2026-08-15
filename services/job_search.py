import os

import httpx

_SERPER_URL = "https://google.serper.dev/search"


def search_jobs(query: str, location: str = "", num_results: int = 10) -> list[dict]:
    """Search live job listings via Serper (Google search wrapper).

    Returns a normalized list of {title, company, link, snippet}.
    """
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is not set")

    full_query = f"{query} jobs {location}".strip()
    response = httpx.post(
        _SERPER_URL,
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": full_query, "num": num_results},
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()

    return [_normalize_result(result) for result in data.get("organic", [])[:num_results]]


def _normalize_result(result: dict) -> dict:
    return {
        "title": result.get("title", ""),
        "company": "",
        "link": result.get("link", ""),
        "snippet": result.get("snippet", ""),
        "description": result.get("snippet", ""),
        "location": "",
        "required_skills": [],
        "min_education_level": "",
        "min_years_experience": 0,
    }
