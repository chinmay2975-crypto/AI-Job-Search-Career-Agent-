"""Map application-form questions to answers without inventing facts about the candidate.

Precedence (first rule that applies wins):
  1. Standard fields by key: name, email, phone, resume/cover-letter files, profile URLs.
  2. Standard fields by label: LinkedIn / GitHub / website / current company / current title.
  3. EEO / demographic questions -> profile["eeo"] (default: decline to self-identify).
  4. profile["custom_answers"] keyword rules (the user's own screening answers).
  5. Personal questions (compensation, notice, eligibility, availability, consent, ...) with no
     configured answer stop here: they are never sent to the LLM.
  6. "Years of experience overall" -> the bracket containing the profile/resume years.
  7. Resume-grounded LLM answers (one batched call). A dropdown answer is accepted only if the
     LLM's evidence quote appears verbatim in the resume text.
Anything required that no rule can answer is returned as unanswered, so the job goes to
needs_manual instead of being submitted with a guess.
"""

import math
import re
from dataclasses import dataclass, field

from services.candidate_profile import full_name
from services.llm import complete, extract_json


@dataclass
class Question:
    key: str
    label: str
    kind: str  # text | textarea | select | multiselect | file | hidden | checkbox
    required: bool = False
    options: list[str] = field(default_factory=list)
    eeo: bool = False


@dataclass
class Answer:
    question: Question
    value: str | list[str]
    source: str


_SKIP_KEYS = {"preferred_name", "resume_text", "cover_letter_text", "longitude", "latitude"}

_PROFILE_BY_KEY = {
    "first_name": "first_name",
    "last_name": "last_name",
    "email": "email",
    "phone": "phone",
    "location": "current_location",
    "org": "current_company",
    "urls[linkedin]": "linkedin_url",
    "urls[github]": "github_url",
    "urls[portfolio]": "portfolio_url",
    "urls[other]": "portfolio_url",
}

_LABEL_RULES = [
    (("preferred first name", "preferred name"), "first_name"),
    (("linkedin",), "linkedin_url"),
    (("github",), "github_url"),
    (("portfolio", "website", "personal site"), "portfolio_url"),
    (("current company", "current employer", "previous employer", "payroll company", "company name"), "current_company"),
    (("current job title", "current title", "previous job title", "designation"), "current_title"),
]

_EEO_KEYS = [
    (r"\bgender\b", "gender"),
    (r"\bhispanic|\blatino", "hispanic_ethnicity"),
    (r"\brace\b|\bethnic", "race"),
    (r"\bveteran", "veteran_status"),
    (r"\bdisab", "disability_status"),
    (r"\bsexual orientation|\bpronoun|\btransgender", "other"),
]

_DECLINE_PATTERN = re.compile(
    r"decline|don'?t wish|do not wish|prefer not|not to (answer|say|disclose)|choose not|rather not|don'?t want"
)

# Questions about the person rather than their experience. Without a configured answer these
# are unanswerable - the LLM must not fill them in from a resume.
_PERSONAL_PATTERNS = [
    r"\bsalary", r"\bctc\b", r"\bcompensation", r"\bpay\b", r"\bnotice\b", r"\bsponsor", r"\bvisa\b",
    r"\bauthori[sz]", r"\beligib", r"\bright to work", r"\brelocat", r"\bjoining\b", r"\bhow soon",
    r"\bstart date", r"\bearliest", r"\bavailab", r"\bshift", r"\bhybrid", r"\boffice\b", r"\bwfo\b",
    r"\bon-?site", r"\boffer", r"\blast working", r"\bfresher", r"\bcurrently (working|employed)",
    r"\bage\b", r"\bdate of birth", r"\bnationality", r"\bcitizen", r"\bcriminal", r"\bbackground check",
    r"\breference", r"\blocat", r"\breside", r"\baddress", r"\bmarital", r"\bprivacy", r"\bconsent",
    r"\backnowledg", r"\bagree", r"\bcertify", r"\bdeclare", r"\bterms\b", r"\bhear about", r"\breferr",
    r"\bcountr",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def match_option(answer: str, options: list[str]) -> str | None:
    """Pick the option matching `answer`: exact, then whole-word prefix, then containment."""
    a = _norm(answer)
    if not a:
        return None
    normalized = [(o, _norm(o)) for o in options if _norm(o)]
    for o, n in normalized:
        if n == a:
            return o
    for o, n in normalized:
        if re.match(rf"{re.escape(a)}\b", n):
            return o
    for o, n in normalized:
        if a in n:
            return o
    for o, n in normalized:
        if re.search(rf"\b{re.escape(n)}\b", a):
            return o
    return None


def _decline_option(options: list[str]) -> str | None:
    return next((o for o in options if _DECLINE_PATTERN.search(_norm(o))), None)


def _is_personal(label: str) -> bool:
    text = _norm(label)
    return any(re.search(p, text) for p in _PERSONAL_PATTERNS)


# --- individual rules ----------------------------------------------------------------

def _profile_value(profile: dict, key: str) -> str | None:
    return str(profile.get(key, "") or "").strip() or None


def _standard_answer(q: Question, profile: dict, files: dict) -> tuple[bool, str | None]:
    """(rule_matched, answer). A matched rule with no profile value must not fall through to the LLM."""
    key = q.key.casefold()
    label = _norm(q.label)
    if q.kind == "file":
        if key in ("resume", "cover_letter"):
            return True, files.get(key)
        return False, None
    if key == "name":
        return True, full_name(profile) or None
    if key == "comments":
        return True, files.get("cover_letter_text")
    if key in _PROFILE_BY_KEY:
        return True, _profile_value(profile, _PROFILE_BY_KEY[key])
    if re.search(r"\bcountry\b", label) and re.search(r"\bresid|\blive\b|\blocated\b|\bbased\b", label):
        country = _profile_value(profile, "country")
        if q.kind == "select":
            return True, match_option(country or "", q.options)
        return True, country
    if q.kind in ("text", "textarea"):
        for keywords, profile_key in _LABEL_RULES:
            if any(k in label for k in keywords):
                return True, _profile_value(profile, profile_key)
    return False, None


def _eeo_key(q: Question) -> str | None:
    text = _norm(f"{q.key} {q.label}")
    for pattern, eeo_key in _EEO_KEYS:
        if re.search(pattern, text):
            return eeo_key
    return "other" if q.eeo else None


def _eeo_answer(q: Question, eeo_key: str, profile: dict) -> str | None:
    wanted = str((profile.get("eeo") or {}).get(eeo_key, "decline") or "decline")
    if q.kind not in ("select", "multiselect"):
        return None if wanted == "decline" else wanted
    if wanted.casefold() == "decline":
        return _decline_option(q.options)
    return match_option(wanted, q.options)


def _custom_rule(q: Question, profile: dict) -> tuple[bool, str | None]:
    """(rule_matched, answer). A matched rule whose answer doesn't fit the options -> (True, None)."""
    label = _norm(q.label)
    for rule in profile.get("custom_answers") or []:
        answer = str(rule.get("answer", "") or "").strip()
        if not answer or not any(_norm(k) in label for k in rule.get("match") or []):
            continue
        if q.kind == "select":
            return True, match_option(answer, q.options)
        if q.kind == "multiselect":
            picked = [match_option(part, q.options) for part in answer.split(";")]
            return True, ";".join(p for p in picked if p) if all(picked) else None
        return True, answer
    return False, None


def _bracket_bounds(option: str) -> tuple[float, float] | None:
    text = _norm(option)
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return None
    if "<" in text or "less than" in text or "under" in text or "below" in text:
        return 0.0, nums[0]
    if "+" in text or "more than" in text or "above" in text or "or more" in text or "over" in text:
        return nums[0], math.inf
    if len(nums) >= 2:
        return nums[0], nums[1]
    return nums[0], nums[0] + 1


def _is_overall_experience(label: str) -> bool:
    text = _norm(label)
    if not re.search(r"\byears?\b.*\bexperience|\bexperience\b.*\byears?\b", text):
        return False
    return bool(re.search(r"\boverall\b|\btotal\b|\bprofessional\b|\bwork experience\b", text)) or bool(
        re.search(r"years? of (work |professional )?experience( do you have)?\s*\??$", text)
    )


def _experience_answer(q: Question, years: float) -> str | None:
    if q.kind in ("text", "textarea"):
        return f"{years:g}"
    if q.kind != "select":
        return None
    for strict in (True, False):
        for option in q.options:
            bounds = _bracket_bounds(option)
            if not bounds:
                continue
            lo, hi = bounds
            if (lo <= years < hi) if strict else (lo <= years <= hi):
                return option
    return None


# --- LLM (resume-grounded) --------------------------------------------------------------

_LLM_PROMPT = """You are filling in a job application on behalf of a candidate, using ONLY facts
stated in their resume. Never invent experience, numbers, dates, or opinions the resume does not support.

Resume:
{resume}

Job: {title} at {company}

For each question below return an object:
  {{"key": <question key>, "status": "answered" | "unknown", "answer": <string>, "evidence": <string>}}
Rules:
- Dropdown questions list their options; "answer" must be exactly one option. "evidence" must be a
  sentence or phrase copied VERBATIM from the resume that proves the answer. If the resume doesn't
  prove it either way, use status "unknown".
- Free-text questions: write 2-4 sentences in first person, grounded in the resume. If the question asks
  for anything the resume doesn't contain (personal details, preferences, compensation, dates,
  availability), use status "unknown".
Return a JSON list only.

Questions:
{questions}
"""


def _evidence_in_resume(evidence: str, resume_text: str) -> bool:
    ev = _norm(evidence)
    return len(ev) >= 10 and ev in _norm(resume_text)


def _llm_answers(questions: list[Question], candidate: dict, job: dict) -> dict[str, str]:
    if not questions:
        return {}
    resume_text = candidate.get("resume_text", "")
    if not resume_text.strip():
        return {}

    listed = []
    for q in questions:
        entry = f'- key: "{q.key}" | type: {"dropdown" if q.options else "free text"} | question: "{q.label}"'
        if q.options:
            entry += f" | options: {q.options}"
        listed.append(entry)

    raw = complete(
        _LLM_PROMPT.format(
            resume=resume_text[:12000],
            title=job.get("title", ""),
            company=job.get("company", ""),
            questions="\n".join(listed),
        ),
        temperature=0.2,
    )
    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        return {}

    by_key = {q.key: q for q in questions}
    results: dict[str, str] = {}
    for item in parsed:
        if not isinstance(item, dict) or item.get("status") != "answered":
            continue
        q = by_key.get(str(item.get("key")))
        answer = str(item.get("answer") or "").strip()
        if not q or not answer:
            continue
        if q.options:
            option = match_option(answer, q.options)
            if option and _evidence_in_resume(str(item.get("evidence") or ""), resume_text):
                results[q.key] = option
        else:
            results[q.key] = answer[:1500]
    return results


# --- entry points ---------------------------------------------------------------------

def answer_questions(
    questions: list[Question], profile: dict, candidate: dict, job: dict, files: dict
) -> tuple[list[Answer], list[Question]]:
    """Return (answers, unanswered_required). `files` = {resume, cover_letter, cover_letter_text}."""
    answers: list[Answer] = []
    unanswered: list[Question] = []
    needs_llm: list[Question] = []

    years = float(profile.get("years_experience") or candidate.get("years_experience") or 0)

    for q in questions:
        if q.kind == "hidden" or q.key.casefold() in _SKIP_KEYS:
            continue

        matched, value = _standard_answer(q, profile, files)
        if matched:
            if value:
                answers.append(Answer(q, value, "profile"))
            elif q.required:
                unanswered.append(q)
            continue

        eeo_key = _eeo_key(q)
        if eeo_key:
            value = _eeo_answer(q, eeo_key, profile)
            if value:
                answers.append(Answer(q, value, "eeo"))
            elif q.required:
                unanswered.append(q)
            continue

        matched, value = _custom_rule(q, profile)
        if matched or _is_personal(q.label) or q.kind in ("file", "checkbox"):
            if value:
                answers.append(Answer(q, value, "custom_answers"))
            elif q.required:
                unanswered.append(q)
            continue

        if years > 0 and _is_overall_experience(q.label):
            value = _experience_answer(q, years)
            if value:
                answers.append(Answer(q, value, "experience"))
                continue

        if q.required:  # optional non-standard questions are left blank rather than LLM-filled
            needs_llm.append(q)

    llm_results = _llm_answers(needs_llm, candidate, job)
    for q in needs_llm:
        if q.key in llm_results:
            answers.append(Answer(q, llm_results[q.key], "resume_llm"))
        elif q.required:
            unanswered.append(q)

    return answers, unanswered


_GREENHOUSE_KINDS = {
    "input_text": "text",
    "textarea": "textarea",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
    "input_file": "file",
    "input_hidden": "hidden",
}


def _greenhouse_question(q: dict, eeo: bool = False) -> Question | None:
    fields = q.get("fields") or []
    if not fields:
        return None
    f = fields[0]  # resume/cover letter list [input_file, textarea]; the file input comes first
    return Question(
        key=str(f.get("name", "")).removesuffix("[]"),
        label=q.get("label", ""),
        kind=_GREENHOUSE_KINDS.get(f.get("type"), "text"),
        required=bool(q.get("required")),
        options=[v.get("label", "") for v in f.get("values") or []],
        eeo=eeo,
    )


def questions_from_greenhouse(job: dict) -> list[Question]:
    """Build Questions from the Greenhouse job API schema kept on the discovered job."""
    questions = [_greenhouse_question(q) for q in job.get("questions") or []]
    for block in job.get("compliance") or []:
        questions.extend(_greenhouse_question(q, eeo=True) for q in block.get("questions") or [])
    return [q for q in questions if q]
