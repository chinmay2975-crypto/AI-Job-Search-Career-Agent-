"""Application submitters, one per auto-submit platform.

Every submitter takes (job, candidate, cover_letter_text, ctx) where
ctx = {"application_id", "profile", "resume_path", "submit": bool} and returns
{"status", "application_url", "detail", "screenshots"} with status one of:

  submitted     - confirmation page/text detected after clicking Submit
  dry_run       - form filled and screenshotted; Submit deliberately not clicked (ctx["submit"] False)
  needs_manual  - nothing was sent: a required question couldn't be answered, a field couldn't be
                  filled, or a CAPTCHA challenge appeared (challenges are never solved automatically)
  unconfirmed   - Submit was clicked but no confirmation was detected; check it by hand, never retried
  failed        - an error before Submit was clicked; nothing was sent

The browser is a plain headless Chromium: no stealth or detection-evasion tricks. Greenhouse and
Lever run invisible reCAPTCHA/hCaptcha that score the session themselves; if either escalates to an
interactive challenge, the application stops at needs_manual.
"""

import asyncio
import os
import re
import textwrap
from pathlib import Path

import httpx
from playwright.async_api import Page, async_playwright

from services.form_answers import Answer, Question, answer_questions, match_option, questions_from_greenhouse

_SANDBOX_APPLY_URL = os.getenv("SANDBOX_APPLY_URL", "http://localhost:8000/sandbox/apply")
_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
_OUTCOME_TIMEOUT_S = 25

_CONFIRMATION_PATTERNS = re.compile(
    r"thank(s| you) for (applying|your application)|application (has been |was )?(submitted|received)|"
    r"we('ve| have) received your application",
    re.IGNORECASE,
)
_CHALLENGE_SELECTORS = [
    'iframe[src*="recaptcha"][src*="bframe"]',          # reCAPTCHA image challenge
    'iframe[src*="hcaptcha"][src*="frame=challenge"]',  # hCaptcha challenge
    'iframe[src*="hcaptcha"][src*="frame=checkbox"]',   # visible hCaptcha checkbox
]


def _result(status: str, detail: str = "", application_url: str = "", screenshots: list[str] | None = None) -> dict:
    return {"status": status, "detail": detail, "application_url": application_url, "screenshots": screenshots or []}


# --- synthetic sandbox ----------------------------------------------------------------

async def submit_synthetic_application(job: dict, candidate: dict, cover_letter_text: str, ctx: dict | None = None) -> dict:
    """Submits against our own local sandbox endpoint. No browser needed since it's our own API.

    Uses an async client: this runs inside the same event loop as the FastAPI server it's
    calling back into, so a blocking sync call here would deadlock the server against itself.
    """
    ctx = ctx or {}
    if not ctx.get("submit", False):
        return _result("dry_run", "DRY_RUN: sandbox application not sent")
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
        return _result("submitted", "sandbox accepted", response.json().get("confirmation_url", ""))
    except httpx.HTTPError as e:
        return _result("failed", str(e))


# --- shared browser helpers -------------------------------------------------------------

async def _screenshot(page: Page, application_id: str, tag: str) -> str:
    path = _LOGS_DIR / "screenshots" / f"{application_id}_{tag}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        return ""
    return str(path)


async def _challenge_visible(page: Page) -> bool:
    for selector in _CHALLENGE_SELECTORS:
        frames = page.locator(selector)
        for i in range(await frames.count()):
            frame = frames.nth(i)
            box = await frame.bounding_box()
            if await frame.is_visible() and box and box["width"] > 50 and box["height"] > 50:
                return True
    return False


async def _visible_validation_errors(page: Page) -> list[str]:
    errors = page.locator('[aria-invalid="true"], .error-message, .helper-text--error, [class*="error"]:not(script)')
    texts = []
    for i in range(min(await errors.count(), 20)):
        el = errors.nth(i)
        if await el.is_visible():
            text = (await el.inner_text()).strip()
            if text and len(text) < 200:
                texts.append(text)
    return texts


async def _wait_for_outcome(page: Page, start_url: str) -> tuple[str, str]:
    """After Submit: (status, detail). Confirmation wins; a challenge or errors -> needs_manual."""
    for _ in range(_OUTCOME_TIMEOUT_S * 2):
        await asyncio.sleep(0.5)
        try:
            body = await page.inner_text("body")
        except Exception:
            continue
        url_changed = page.url != start_url and re.search(r"confirm|thank", page.url, re.IGNORECASE)
        if url_changed or _CONFIRMATION_PATTERNS.search(body):
            return "submitted", "confirmation detected"
        if await _challenge_visible(page):
            return "needs_manual", "CAPTCHA challenge appeared after Submit; not solved automatically - apply by hand"
    errors = await _visible_validation_errors(page)
    if errors:
        return "needs_manual", "form rejected the submission: " + "; ".join(dict.fromkeys(errors))[:500]
    return "unconfirmed", "clicked Submit but no confirmation detected - check the screenshot / your email"


def write_cover_letter_pdf(text: str, path: Path) -> str:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=A4)
    y = A4[1] - 60
    for paragraph in (text or "").split("\n"):
        for line in textwrap.wrap(paragraph, 95) or [""]:
            if y < 60:
                pdf.showPage()
                y = A4[1] - 60
            pdf.drawString(50, y, line)
            y -= 15
    pdf.save()
    return str(path)


def _summarize(answers: list[Answer]) -> str:
    counts: dict[str, int] = {}
    for a in answers:
        counts[a.source] = counts.get(a.source, 0) + 1
    return f"filled {len(answers)} fields (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")"


def _unanswered_detail(unanswered: list[Question]) -> str:
    labels = "; ".join(q.label.strip()[:80] for q in unanswered)
    return f"required questions with no configured answer: {labels} - add them to candidate_profile.yaml custom_answers"


async def _run_in_browser(apply_url: str, ctx: dict, fill_and_submit) -> dict:
    """Open the form, delegate to fill_and_submit(page) -> result; never report success on errors."""
    application_id = ctx.get("application_id", "unknown")
    clicked = {"value": False}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=45000)
            return await fill_and_submit(page, clicked)
        except Exception as e:
            shot = await _screenshot(page, application_id, "error")
            status = "unconfirmed" if clicked["value"] else "failed"
            return _result(status, f"{type(e).__name__}: {e}"[:500], screenshots=[shot] if shot else [])
        finally:
            await browser.close()


async def _submit_or_stop(page: Page, ctx: dict, answers: list[Answer], submit_locator, clicked: dict) -> dict:
    application_id = ctx.get("application_id", "unknown")
    if await _challenge_visible(page):
        shot = await _screenshot(page, application_id, "challenge")
        return _result("needs_manual", "CAPTCHA challenge shown before submit; not solved automatically", screenshots=[shot])

    filled_shot = await _screenshot(page, application_id, "filled")
    if not ctx.get("submit", False):
        return _result("dry_run", f"DRY_RUN: {_summarize(answers)}; Submit not clicked", screenshots=[filled_shot])

    start_url = page.url
    # click() raises before dispatching if the button is obscured (e.g. by a cookie banner), so only
    # mark it clicked afterwards - an exception before this point means nothing was sent.
    await submit_locator.click(timeout=15000)
    clicked["value"] = True
    status, detail = await _wait_for_outcome(page, start_url)
    after_shot = await _screenshot(page, application_id, "after_submit")
    return _result(status, f"{detail}; {_summarize(answers)}", page.url if status == "submitted" else "",
                   screenshots=[filled_shot, after_shot])


# --- Greenhouse -------------------------------------------------------------------------------

async def _select_combobox(page: Page, element_id: str, value: str) -> str:
    """Pick `value` in a Greenhouse react-select combobox; returns the text the control now shows ("" on failure)."""
    combo = page.locator(f'[id="{element_id}"]')
    if await combo.count() == 0:
        return ""
    await combo.click()
    await combo.fill(value[:40])
    options = page.locator(f'[id^="react-select-{element_id}-option-"]')
    try:
        await options.first.wait_for(state="visible", timeout=10000)
    except Exception:
        return ""
    texts = await options.all_inner_texts()
    choice = match_option(value, texts)
    if not choice:
        return ""
    await options.nth(texts.index(choice)).click()
    shown = (await combo.locator('xpath=ancestor::div[contains(@class,"select__control")][1]').inner_text()).strip()
    return shown if shown and (shown in choice or choice in shown) else ""


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


async def _fill_greenhouse_answer(page: Page, answer: Answer, dial_code: str = "") -> bool:
    """Fill one Greenhouse field; False (never an exception) if it can't be filled."""
    try:
        return await _fill_greenhouse_field(page, answer, dial_code)
    except Exception:
        return False


async def _fill_greenhouse_field(page: Page, answer: Answer, dial_code: str) -> bool:
    q = answer.question
    if q.key == "location":  # rendered as a location autocomplete, not a plain input
        return bool(await _select_combobox(page, "candidate-location", str(answer.value)))

    field = page.locator(f'[id="{q.key}"]')
    if await field.count() == 0:
        return False

    if q.kind == "file":
        await field.set_input_files(str(answer.value))
        await page.wait_for_timeout(1500)
        return await page.get_by_text(Path(str(answer.value)).name).count() > 0
    if q.kind == "select":
        return bool(await _select_combobox(page, q.key, str(answer.value)))
    if q.kind == "multiselect":
        return all([bool(await _select_combobox(page, q.key, v)) for v in str(answer.value).split(";") if v])

    value = str(answer.value)
    if q.key == "phone":
        # The country picker already carries the dial code; the input reformats what's typed.
        if dial_code and value.replace(" ", "").startswith(dial_code):
            value = value.replace(" ", "")[len(dial_code):]
        await field.fill(value)
        return _digits(await field.input_value()).endswith(_digits(value)[-7:])

    await field.fill(value)
    return (await field.input_value()) == value


async def submit_greenhouse_application(job: dict, candidate: dict, cover_letter_text: str, ctx: dict) -> dict:
    profile = ctx.get("profile") or {}
    application_id = ctx.get("application_id", "unknown")
    # Employers see the attachment's filename, so name it like a person would; the per-application
    # directory keeps it unique.
    name_part = re.sub(r"[^A-Za-z0-9]+", "_", f"{profile.get('first_name', '')} {profile.get('last_name', '')}").strip("_")
    cover_letter_path = write_cover_letter_pdf(
        cover_letter_text,
        _LOGS_DIR / "cover_letters" / application_id / f"Cover_Letter{'_' + name_part if name_part else ''}.pdf",
    )
    files = {"resume": ctx.get("resume_path"), "cover_letter": cover_letter_path, "cover_letter_text": cover_letter_text}

    answers, unanswered = answer_questions(questions_from_greenhouse(job), profile, candidate, job, files)
    if unanswered:
        return _result("needs_manual", _unanswered_detail(unanswered))

    async def fill_and_submit(page: Page, clicked: dict) -> dict:
        await page.wait_for_selector('[id="first_name"]', timeout=20000)
        not_filled = []

        # The phone country picker isn't in the API schema but is required on the page; set it
        # before the phone number, since changing it reformats the number field.
        dial_code = ""
        if await page.locator('[id="country"]').count():
            shown = await _select_combobox(page, "country", str(profile.get("country", "")))
            if not shown:
                not_filled.append("Country (phone)")
            dial_code = (re.search(r"\+\d+", shown) or [""])[0]

        for answer in answers:
            if not await _fill_greenhouse_answer(page, answer, dial_code) and answer.question.required:
                not_filled.append(answer.question.label)

        if not_filled:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", "could not fill required fields: " + "; ".join(not_filled), screenshots=[shot])

        return await _submit_or_stop(page, ctx, answers, page.locator('button[type="submit"]').first, clicked)

    return await _run_in_browser(job.get("apply_url") or job.get("url", ""), ctx, fill_and_submit)


# --- Lever ------------------------------------------------------------------------------------------

_LEVER_FIELDS_JS = """
form => {
  const groups = {};
  for (const el of form.querySelectorAll('input, textarea, select')) {
    if (!el.name || el.type === 'hidden' || el.type === 'submit' || el.name.startsWith('consent')) continue;
    // Skip sections the page isn't showing (e.g. a collapsed US EEO survey). File inputs are
    // hidden behind styled buttons by design, and custom radios may hide the input but not its label.
    const shown = el.type === 'file' || (el.closest('label') || el).offsetParent !== null;
    if (!shown) continue;
    // Radio/checkbox options sit in their own <li>, so look for the question container first.
    const q = el.closest('.application-question') || el.closest('li');
    const labelEl = q ? q.querySelector('.application-label, .text') : null;
    const rawLabel = labelEl ? labelEl.innerText : (el.getAttribute('aria-label') || el.name);
    const g = groups[el.name] || (groups[el.name] = {
      key: el.name, label: rawLabel.replace(/[\\u2731*]/g, '').replace(/\\s+/g, ' ').trim(),
      required: false, tag: el.tagName, type: el.type, options: []
    });
    g.required = g.required || el.required || rawLabel.includes('\\u2731');
    if (el.tagName === 'SELECT') {
      g.options = Array.from(el.options).map(o => o.text.trim()).filter(t => t && !/^select/i.test(t));
    } else if (el.type === 'radio' || el.type === 'checkbox') {
      g.options.push(el.value);
    }
  }
  return Object.values(groups);
}
"""


def _lever_kind(field: dict) -> str:
    if field["type"] == "file":
        return "file"
    if field["tag"] == "SELECT" or field["type"] == "radio":
        return "select"
    if field["type"] == "checkbox":
        return "multiselect" if len(field["options"]) > 1 else "checkbox"
    return "textarea" if field["tag"] == "TEXTAREA" else "text"


_FIELD_TIMEOUT_MS = 5000


async def _fill_lever_answer(page: Page, answer: Answer) -> bool:
    """Fill one Lever field; False (never an exception) if it can't be filled."""
    try:
        return await _fill_lever_field(page, answer)
    except Exception:
        return False


async def _fill_lever_field(page: Page, answer: Answer) -> bool:
    q = answer.question
    selector_name = q.key.replace('"', '\\"')
    value = str(answer.value).replace('"', '\\"')
    if q.kind == "file":
        return True  # uploaded first, before the other fields (Lever autofills from the resume)
    if q.kind == "select":
        select = page.locator(f'select[name="{selector_name}"]')
        if await select.count():
            await select.select_option(label=str(answer.value), timeout=_FIELD_TIMEOUT_MS)
            return True
        radio = page.locator(f'input[type="radio"][name="{selector_name}"][value="{value}"]')
        if await radio.count():
            await radio.check(timeout=_FIELD_TIMEOUT_MS)
            return await radio.is_checked()
        return False
    if q.kind == "multiselect":
        for part in str(answer.value).split(";"):
            box = page.locator(f'input[type="checkbox"][name="{selector_name}"][value="{part}"]')
            if not await box.count():
                return False
            await box.check(timeout=_FIELD_TIMEOUT_MS)
        return True

    field = page.locator(f'[name="{selector_name}"]').first
    await field.fill(str(answer.value), timeout=_FIELD_TIMEOUT_MS)
    return (await field.input_value()) == str(answer.value)


async def submit_lever_application(job: dict, candidate: dict, cover_letter_text: str, ctx: dict) -> dict:
    profile = ctx.get("profile") or {}
    application_id = ctx.get("application_id", "unknown")
    files = {"resume": ctx.get("resume_path"), "cover_letter_text": cover_letter_text}

    async def fill_and_submit(page: Page, clicked: dict) -> dict:
        await page.wait_for_selector('input[name="name"]', timeout=20000)

        resume_input = page.locator('input[type="file"][name="resume"]')
        if await resume_input.count() and files["resume"]:
            await resume_input.set_input_files(files["resume"])
            await page.wait_for_timeout(3000)  # let Lever's resume parser finish before overwriting its autofill

        raw_fields = await page.locator("form").first.evaluate(_LEVER_FIELDS_JS)
        questions = [
            Question(key=f["key"], label=f["label"], kind=_lever_kind(f), required=bool(f["required"]),
                     options=f["options"], eeo=f["key"].startswith("eeo["))
            for f in raw_fields
        ]
        answers, unanswered = answer_questions(questions, profile, candidate, job, files)
        if unanswered:
            shot = await _screenshot(page, application_id, "unanswered")
            return _result("needs_manual", _unanswered_detail(unanswered), screenshots=[shot])

        not_filled = [a.question.label for a in answers
                      if not await _fill_lever_answer(page, a) and a.question.required]
        if not_filled:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", "could not fill required fields: " + "; ".join(not_filled), screenshots=[shot])

        return await _submit_or_stop(page, ctx, answers, page.locator("#btn-submit"), clicked)

    return await _run_in_browser(job.get("apply_url") or f"{job.get('url', '')}/apply", ctx, fill_and_submit)
