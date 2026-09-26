"""Application submitters, one per auto-submit platform.

Every submitter takes (job, candidate, cover_letter_text, ctx) where
ctx = {"application_id", "profile", "resume_path", "submit": bool} and returns
{"status", "application_url", "detail", "screenshots"} with status one of:

  submitted             - confirmation page/text appeared after clicking Submit
  dry_run               - form filled and screenshotted; Submit deliberately not clicked (ctx["submit"] False)
  needs_manual          - nothing was sent: a required question couldn't be answered or a field couldn't
                          be filled (the user answers in Streamlit and approves)
  verification_required - the site asked for a human check (CAPTCHA, emailed security code); nothing was
                          sent and the agent never completes these - the user applies on the site. Not
                          retried automatically, since each attempt can trigger another code email.
  unconfirmed           - Submit was clicked but neither confirmation nor a check appeared; never retried
  failed                - an error before Submit was clicked; nothing was sent

The browser is a plain headless Chromium: no stealth or detection-evasion tricks. Greenhouse, Lever and
Ashby run invisible reCAPTCHA/hCaptcha that score the session themselves; when that escalates to a
human check, the application stops at verification_required.
"""

import asyncio
import os
import re
import textwrap
from dataclasses import asdict
from pathlib import Path

import httpx
from playwright.async_api import Page, async_playwright

from services.form_answers import (
    Answer,
    Question,
    answer_questions,
    match_option,
    questions_from_greenhouse,
    questions_from_page_fields,
    questions_from_workable,
)

_SANDBOX_APPLY_URL = os.getenv("SANDBOX_APPLY_URL", "http://localhost:8010/sandbox/apply")
_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
_OUTCOME_TIMEOUT_S = 25

_CONFIRMATION_PATTERNS = re.compile(
    r"thank(s| you) for (applying|your application)|"
    r"application (has been |was )?(successfully )?(submitted|received|sent)|successfully submitted|"
    r"we('ve| have) received your application",
    re.IGNORECASE,
)
# Human checks some sites add after Submit, e.g. Greenhouse's emailed security code.
_VERIFICATION_PATTERNS = re.compile(
    r"verification code (was|has been) sent|enter the [\w-]+ code|confirm you('| a)re a human|security code|"
    r"verify (that )?you('| a)re (a )?human|are you a robot",
    re.IGNORECASE,
)
_VERIFICATION_DETAIL = ("the site asked for a human check (CAPTCHA or an emailed security code) - the agent never "
                        "completes these; nothing was submitted. Apply on the site yourself")
_CHALLENGE_SELECTORS = [
    'iframe[src*="recaptcha"][src*="bframe"]',          # reCAPTCHA image challenge
    'iframe[src*="hcaptcha"][src*="frame=challenge"]',  # hCaptcha challenge
    'iframe[src*="hcaptcha"][src*="frame=checkbox"]',   # visible hCaptcha checkbox
]


def _result(status: str, detail: str = "", application_url: str = "", screenshots: list[str] | None = None,
            pending_questions: list[Question] | None = None) -> dict:
    return {
        "status": status, "detail": detail, "application_url": application_url, "screenshots": screenshots or [],
        # Unanswered required questions, shown in the Applications tab for the user to answer and approve.
        "pending_questions": [asdict(q) for q in pending_questions or []],
    }


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


def _newly_matches(pattern: re.Pattern, before: str, after: str) -> bool:
    """True if `pattern` matches more often after Submit than before - so wording that was already on
    the page (e.g. "thank you for applying" in a job description) never counts as an outcome."""
    return len(pattern.findall(after)) > len(pattern.findall(before))


async def _wait_for_outcome(page: Page, start_url: str, before_text: str = "") -> tuple[str, str]:
    """After Submit: (status, detail). Confirmation wins; a human check -> verification_required."""
    for _ in range(_OUTCOME_TIMEOUT_S * 2):
        await asyncio.sleep(0.5)
        try:
            body = await page.inner_text("body")
        except Exception:
            continue
        url_changed = page.url != start_url and re.search(r"confirm|thank|success", page.url, re.IGNORECASE)
        if url_changed or _newly_matches(_CONFIRMATION_PATTERNS, before_text, body):
            return "submitted", "confirmation detected"
        if await _challenge_visible(page) or _newly_matches(_VERIFICATION_PATTERNS, before_text, body):
            return "verification_required", _VERIFICATION_DETAIL
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
    return f"awaiting your answers to: {labels}"


_COOKIE_DECLINE = re.compile(
    r"^\s*(decline all|reject all|decline|reject|only necessary|necessary only|use necessary cookies only|dismiss)\s*$",
    re.IGNORECASE,
)


async def _decline_cookie_banners(page: Page) -> None:
    """Close cookie-consent overlays that would block clicks, always choosing the no-tracking option.
    Cookies are never accepted on the user's behalf; with only an "Accept" button the banner stays."""
    try:
        workable = page.locator('[data-ui="cookie-consent-decline"]')
        if await workable.count():
            await workable.first.click(timeout=3000)
            return
        buttons = page.get_by_role("button", name=_COOKIE_DECLINE)
        for i in range(await buttons.count()):
            button = buttons.nth(i)
            context = await button.evaluate(
                "b => (b.closest('[role=dialog], [class*=cookie], [id*=cookie], [class*=consent], div') || b)"
                ".innerText.slice(0, 500)"
            )
            if re.search(r"cookie", context, re.IGNORECASE) and await button.is_visible():
                await button.click(timeout=3000)
                return
    except Exception:
        pass


async def _run_in_browser(apply_url: str, ctx: dict, fill_and_submit) -> dict:
    """Open the form, delegate to fill_and_submit(page) -> result; never report success on errors."""
    application_id = ctx.get("application_id", "unknown")
    clicked = {"value": False}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(apply_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)  # consent banners render after load
            await _decline_cookie_banners(page)
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
        return _result("verification_required", _VERIFICATION_DETAIL, screenshots=[shot])

    filled_shot = await _screenshot(page, application_id, "filled")
    if not ctx.get("submit", False):
        return _result("dry_run", f"DRY_RUN: {_summarize(answers)}; Submit not clicked", screenshots=[filled_shot])

    start_url = page.url
    before_text = await page.inner_text("body")
    # click() raises before dispatching if the button is obscured (e.g. by a cookie banner), so only
    # mark it clicked afterwards - an exception before this point means nothing was sent.
    await submit_locator.click(timeout=15000)
    clicked["value"] = True
    status, detail = await _wait_for_outcome(page, start_url, before_text)
    after_shot = await _screenshot(page, application_id, "after_submit")
    return _result(status, f"{detail}; {_summarize(answers)}", page.url if status == "submitted" else "",
                   screenshots=[filled_shot, after_shot])


# --- Greenhouse -------------------------------------------------------------------------------

async def _select_combobox(page: Page, element_id: str, value: str) -> str:
    """Pick `value` in a Greenhouse react-select combobox; returns the text the control now shows ("" on failure).
    Retried once: the option list re-renders while it filters, so a first attempt can miss."""
    for attempt in range(2):
        try:
            shown = await _select_combobox_once(page, element_id, value)
        except Exception:
            shown = ""
        if shown:
            return shown
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    return ""


async def _select_combobox_once(page: Page, element_id: str, value: str) -> str:
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
        return await _upload_greenhouse_file(page, q.key, str(answer.value))
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


async def _upload_greenhouse_file(page: Page, field_id: str, path: str) -> bool:
    """Attach a file the way a person does (the Attach button's file chooser). Some boards' upload
    code only initializes through that path; setting the hidden input directly is the fallback."""
    field = page.locator(f'[id="{field_id}"]')
    container = field.locator("xpath=ancestor::div[.//button][1]")
    attach = container.locator("button", has_text="Attach").first
    try:
        async with page.expect_file_chooser(timeout=5000) as chooser_info:
            await attach.click(timeout=5000)
        await (await chooser_info.value).set_files(path)
    except Exception:
        await field.set_input_files(path)
    await page.wait_for_timeout(3000)
    # The upload widget re-renders after a successful upload (the Attach button is replaced), so
    # check the page: our file's name is listed and no upload error is shown.
    return (await page.get_by_text(Path(path).name).count() > 0
            and await page.get_by_text("Cannot read properties").count() == 0)


# Required inputs actually on the page (with their current fill state). React-select comboboxes keep
# an empty <input> after a choice, so their "filled" state comes from the rendered selection.
_GREENHOUSE_REQUIRED_JS = """
() => Array.from(document.querySelectorAll('form input, form textarea'))
  .filter(e => e.id && e.type !== 'hidden' && e.type !== 'file'
               && (e.required || e.getAttribute('aria-required') === 'true'))
  .map(e => {
    const combobox = e.getAttribute('role') === 'combobox';
    const control = combobox ? e.closest('.select__control') : null;
    const filled = combobox
      ? !!(control && control.querySelector('.select__single-value, .select__multi-value'))
      : e.value.trim() !== '';
    const labelEl = document.querySelector('label[for="' + e.id + '"]');
    return {id: e.id, label: labelEl ? labelEl.innerText : '', combobox: combobox, filled: filled};
  })
"""


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
    schema_questions = questions_from_greenhouse(job)

    async def fill_and_submit(page: Page, clicked: dict) -> dict:
        await page.wait_for_selector('[id="first_name"]', timeout=20000)
        await page.wait_for_timeout(1000)  # let the upload widgets initialize

        # The API schema doesn't describe everything (e.g. the Education section), so the live
        # form's required fields are added as questions too - answered or held like any other.
        page_fields = await page.evaluate(_GREENHOUSE_REQUIRED_JS)
        questions = schema_questions + questions_from_page_fields(schema_questions, page_fields)
        answers, unanswered = answer_questions(
            questions, profile, candidate, job, files,
            overrides=ctx.get("overrides"), saved_answers=ctx.get("saved_answers"),
        )
        if unanswered:
            return _result("needs_manual", _unanswered_detail(unanswered), pending_questions=unanswered)

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

        # Last line of defence: never click Submit while the page still shows an empty required field.
        still_empty = [f for f in await page.evaluate(_GREENHOUSE_REQUIRED_JS) if not f["filled"]]
        if still_empty:
            shot = await _screenshot(page, application_id, "unfilled")
            pending = questions_from_page_fields([], still_empty)
            return _result("needs_manual", _unanswered_detail(pending), screenshots=[shot], pending_questions=pending)

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
        answers, unanswered = answer_questions(
            questions, profile, candidate, job, files,
            overrides=ctx.get("overrides"), saved_answers=ctx.get("saved_answers"),
        )
        if unanswered:
            shot = await _screenshot(page, application_id, "unanswered")
            return _result("needs_manual", _unanswered_detail(unanswered), screenshots=[shot],
                           pending_questions=unanswered)

        not_filled = [a.question.label for a in answers
                      if not await _fill_lever_answer(page, a) and a.question.required]
        if not_filled:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", "could not fill required fields: " + "; ".join(not_filled), screenshots=[shot])

        return await _submit_or_stop(page, ctx, answers, page.locator("#btn-submit"), clicked)

    return await _run_in_browser(job.get("apply_url") or f"{job.get('url', '')}/apply", ctx, fill_and_submit)


# --- shared helpers for plain-HTML forms (Workable, Ashby) --------------------------------------------

# Required text-like inputs still empty on the page: the last check before Submit is clicked.
_EMPTY_REQUIRED_JS = """
() => Array.from(document.querySelectorAll('input, textarea, select'))
  .filter(e => (e.required || e.getAttribute('aria-required') === 'true')
               && !['hidden', 'file', 'checkbox', 'radio', 'submit'].includes(e.type)
               && e.name !== 'g-recaptcha-response' && e.offsetParent !== null
               && !String(e.value || '').trim())
  .map(e => {
    const label = e.id ? document.querySelector('label[for="' + e.id + '"]') : null;
    const text = label ? label.innerText : (e.getAttribute('aria-label') || e.name || e.id);
    return {key: e.name || e.id, label: text.replace(/\\*/g, '').trim()};
  })
"""


async def _empty_required_questions(page: Page) -> list[Question]:
    fields = await page.evaluate(_EMPTY_REQUIRED_JS)
    return [Question(key=f["key"], label=f["label"], kind="text", required=True) for f in fields if f["key"]]


async def _upload_file(page: Page, file_input, path: str, button=None) -> bool:
    """Attach through the page's own upload button (file chooser) when there is one, else set the input."""
    try:
        if button is None or not await button.count():
            raise LookupError("no upload button")
        async with page.expect_file_chooser(timeout=5000) as chooser_info:
            await button.first.click(timeout=5000)
        await (await chooser_info.value).set_files(path)
    except Exception:
        await file_input.set_input_files(path)
    await page.wait_for_timeout(3000)
    return await page.get_by_text(Path(path).name).count() > 0


async def _check_option(page: Page, input_locator) -> bool:
    """Tick a checkbox/radio. Styled forms hide the real input: Workable wraps it in an ARIA
    widget (role=checkbox/radio, aria-checked), others in a clickable label."""
    if not await input_locator.count():
        return False
    target = input_locator.first
    widget = target.locator('xpath=ancestor::*[@role="checkbox" or @role="radio"][1]')

    async def ticked() -> bool:
        if await widget.count() and await widget.first.get_attribute("aria-checked") == "true":
            return True
        return await target.is_checked()

    if await ticked():
        return True
    if await widget.count():
        await widget.first.click(timeout=_FIELD_TIMEOUT_MS)
        return await ticked()
    try:
        await target.check(timeout=_FIELD_TIMEOUT_MS)
    except Exception:
        label = page.locator("label", has=target)
        if await label.count():
            await label.first.click(timeout=_FIELD_TIMEOUT_MS)
    return await ticked()


async def _fill_text(field, value: str, digits_only: bool = False) -> bool:
    await field.fill(value, timeout=_FIELD_TIMEOUT_MS)
    actual = await field.input_value()
    if digits_only:
        return _digits(actual).endswith(_digits(value)[-7:])
    return actual == value


# --- Workable -------------------------------------------------------------------------------------------

async def _fill_workable_field(page: Page, answer: Answer) -> bool:
    q = answer.question
    value = str(answer.value)
    try:
        if q.kind == "file":
            return q.key == "resume" and await _upload_file(page, page.locator('input[data-ui="resume"]'), value)
        if q.kind in ("select", "multiselect") and q.option_ids:
            for part in value.split(";"):
                if part not in q.options:
                    return False
                if not await _check_option(page, page.locator(f'input[name="{q.option_ids[q.options.index(part)]}"]')):
                    return False
            return True
        if q.kind == "select":  # yes/no: radios named after the question
            radios = page.locator(f'input[name="{q.key}"]')
            for i in range(await radios.count()):
                label = page.locator("label", has=radios.nth(i))
                if await label.count() and (await label.first.inner_text()).strip().casefold() == value.casefold():
                    return await _check_option(page, radios.nth(i))
            return False
        return await _fill_text(page.locator(f'[name="{q.key}"]').first, value, digits_only=q.key == "phone")
    except Exception:
        return False


async def submit_workable_application(job: dict, candidate: dict, cover_letter_text: str, ctx: dict) -> dict:
    profile = ctx.get("profile") or {}
    application_id = ctx.get("application_id", "unknown")
    files = {"resume": ctx.get("resume_path"), "cover_letter_text": cover_letter_text}
    questions = questions_from_workable(job)

    async def fill_and_submit(page: Page, clicked: dict) -> dict:
        await page.wait_for_selector('[name="firstname"], [name="email"]', timeout=25000)
        await page.wait_for_timeout(1000)
        answers, unanswered = answer_questions(
            questions, profile, candidate, job, files,
            overrides=ctx.get("overrides"), saved_answers=ctx.get("saved_answers"),
        )
        if unanswered:
            shot = await _screenshot(page, application_id, "unanswered")
            return _result("needs_manual", _unanswered_detail(unanswered), screenshots=[shot], pending_questions=unanswered)

        # Resume first: some forms parse it and pre-fill fields, which our answers then overwrite.
        ordered = sorted(answers, key=lambda a: a.question.kind != "file")
        not_filled = [a.question.label for a in ordered
                      if not await _fill_workable_field(page, a) and a.question.required]
        if not_filled:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", "could not fill required fields: " + "; ".join(not_filled), screenshots=[shot])

        still_empty = await _empty_required_questions(page)
        if still_empty:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", _unanswered_detail(still_empty), screenshots=[shot], pending_questions=still_empty)

        return await _submit_or_stop(page, ctx, answers, page.locator('button[type="submit"]').first, clicked)

    return await _run_in_browser(job.get("apply_url") or f"{job.get('url', '')}apply/", ctx, fill_and_submit)


# --- Ashby ----------------------------------------------------------------------------------------------

_ASHBY_FIELDS_JS = """
() => {
  const groups = {};
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const key = el.name || el.id;
    if (!key || el.type === 'hidden' || el.type === 'submit' || key === 'g-recaptcha-response') continue;
    const shown = el.type === 'file' || (el.closest('label') || el).offsetParent !== null;
    if (!shown) continue;
    const own = el.id ? document.querySelector('label[for="' + el.id + '"]') : null;
    const fieldset = el.closest('fieldset');
    const legend = fieldset ? fieldset.querySelector('legend') : null;
    const isChoice = el.type === 'radio' || el.type === 'checkbox';
    const rawLabel = isChoice ? (legend ? legend.innerText : key) : (own ? own.innerText : key);
    const g = groups[key] || (groups[key] = {
      key: key, label: rawLabel.replace(/\\*/g, '').trim(), required: false,
      tag: el.tagName, type: el.type, options: []
    });
    g.required = g.required || el.required || el.getAttribute('aria-required') === 'true';
    if (el.tagName === 'SELECT') {
      g.options = Array.from(el.options).map(o => o.text.trim()).filter(t => t && !/^select/i.test(t));
    } else if (isChoice) {
      const optionLabel = own || el.closest('label');
      g.options.push(optionLabel ? optionLabel.innerText.trim() : el.value);
    }
  }
  return Object.values(groups);
}
"""


async def _fill_ashby_field(page: Page, answer: Answer) -> bool:
    q = answer.question
    value = str(answer.value)
    try:
        field = page.locator(f'[id="{q.key}"], [name="{q.key}"]')
        if q.kind == "file":
            container = field.first.locator("xpath=ancestor::div[.//button][1]")
            return await _upload_file(page, field.first, value, container.locator("button", has_text=re.compile("upload", re.I)))
        if q.kind in ("select", "multiselect"):
            select = page.locator(f'select[name="{q.key}"], select[id="{q.key}"]')
            if await select.count():
                await select.first.select_option(label=value, timeout=_FIELD_TIMEOUT_MS)
                return True
            choices = page.locator(f'input[name="{q.key}"]')
            for part in value.split(";"):
                matched = False
                for i in range(await choices.count()):
                    choice = choices.nth(i)
                    choice_id = await choice.get_attribute("id")
                    label = page.locator(f'label[for="{choice_id}"]') if choice_id else page.locator("label", has=choice)
                    if await label.count() and (await label.first.inner_text()).strip().casefold() == part.casefold():
                        matched = await _check_option(page, choice)
                        break
                if not matched:
                    return False
            return True
        return await _fill_text(field.first, value, digits_only=q.key == "_systemfield_phone")
    except Exception:
        return False


async def submit_ashby_application(job: dict, candidate: dict, cover_letter_text: str, ctx: dict) -> dict:
    profile = ctx.get("profile") or {}
    application_id = ctx.get("application_id", "unknown")
    files = {"resume": ctx.get("resume_path"), "cover_letter_text": cover_letter_text}

    async def fill_and_submit(page: Page, clicked: dict) -> dict:
        await page.wait_for_selector("#_systemfield_name, #_systemfield_email", timeout=25000)
        await page.wait_for_timeout(1500)
        raw_fields = await page.evaluate(_ASHBY_FIELDS_JS)
        questions = [
            Question(key=f["key"], label=f["label"], kind=_lever_kind(f), required=bool(f["required"]), options=f["options"])
            for f in raw_fields
        ]
        answers, unanswered = answer_questions(
            questions, profile, candidate, job, files,
            overrides=ctx.get("overrides"), saved_answers=ctx.get("saved_answers"),
        )
        if unanswered:
            shot = await _screenshot(page, application_id, "unanswered")
            return _result("needs_manual", _unanswered_detail(unanswered), screenshots=[shot], pending_questions=unanswered)

        ordered = sorted(answers, key=lambda a: a.question.kind != "file")  # resume first (Ashby may autofill from it)
        not_filled = [a.question.label for a in ordered
                      if not await _fill_ashby_field(page, a) and a.question.required]
        if not_filled:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", "could not fill required fields: " + "; ".join(not_filled), screenshots=[shot])

        still_empty = await _empty_required_questions(page)
        if still_empty:
            shot = await _screenshot(page, application_id, "unfilled")
            return _result("needs_manual", _unanswered_detail(still_empty), screenshots=[shot], pending_questions=still_empty)

        submit = page.get_by_role("button", name=re.compile(r"submit application", re.I))
        return await _submit_or_stop(page, ctx, answers, submit, clicked)

    return await _run_in_browser(job.get("apply_url") or f"{job.get('url', '')}/application", ctx, fill_and_submit)
