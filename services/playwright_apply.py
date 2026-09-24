import os

import httpx
from playwright.async_api import async_playwright

_SANDBOX_APPLY_URL = os.getenv("SANDBOX_APPLY_URL", "http://localhost:8000/sandbox/apply")


async def submit_synthetic_application(job: dict, candidate: dict, cover_letter_text: str) -> dict:
    """Submits against our own local sandbox endpoint. No browser needed since it's our own API.

    Uses an async client: this runs inside the same event loop as the FastAPI server it's
    calling back into, so a blocking sync call here would deadlock the server against itself.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                _SANDBOX_APPLY_URL,
                json={
                    "job_url": job.get("link") or job.get("url", ""),
                    "candidate_id": candidate.get("candidate_id", ""),
                    "cover_letter_text": cover_letter_text,
                },
            )
        response.raise_for_status()
        data = response.json()
        return {"success": True, "application_url": data.get("confirmation_url", ""), "error": ""}
    except httpx.HTTPError as e:
        return {"success": False, "application_url": "", "error": str(e)}


# NOTE: Greenhouse/Lever form field selectors below are best-effort based on each platform's
# common embed structure. Verify against a real live posting (with DRY_RUN=true first) before
# ever running with DRY_RUN=false against a real employer.


async def submit_greenhouse_application(job: dict, candidate: dict, cover_letter_text: str) -> dict:
    url = job.get("link") or job.get("url", "")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")

            await _fill_if_present(page, "#first_name", candidate.get("first_name", ""))
            await _fill_if_present(page, "#last_name", candidate.get("last_name", ""))
            await _fill_if_present(page, "#email", candidate.get("email", ""))
            await _fill_if_present(page, "textarea[name='cover_letter_text']", cover_letter_text)

            confirmation_url = page.url
            await browser.close()
            return {"success": True, "application_url": confirmation_url, "error": ""}
    except Exception as e:
        return {"success": False, "application_url": "", "error": str(e)}


async def submit_lever_application(job: dict, candidate: dict, cover_letter_text: str) -> dict:
    url = job.get("link") or job.get("url", "")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")

            await _fill_if_present(page, "input[name='name']", candidate.get("name", ""))
            await _fill_if_present(page, "input[name='email']", candidate.get("email", ""))
            await _fill_if_present(page, "textarea[name='comments']", cover_letter_text)

            confirmation_url = page.url
            await browser.close()
            return {"success": True, "application_url": confirmation_url, "error": ""}
    except Exception as e:
        return {"success": False, "application_url": "", "error": str(e)}


async def _fill_if_present(page, selector: str, value: str) -> None:
    if not value:
        return
    locator = page.locator(selector)
    if await locator.count() > 0:
        await locator.first.fill(value)
