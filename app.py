import os
from collections import Counter
from pathlib import Path

import httpx
import streamlit as st

API_BASE = os.getenv("CAREER_AGENT_API_URL", "http://localhost:8010")

STATUSES = [
    "(active)", "needs_manual", "verification_required", "apply_yourself", "pending_approval", "submitted",
    "unconfirmed", "dry_run",
    "approved", "failed", "blocked", "rejected_by_user", "draft", "superseded",
]

PLATFORM_NAMES = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workable": "Workable",
    "smartrecruiters": "SmartRecruiters", "workday": "Workday", "internshala": "Internshala",
    "linkedin": "LinkedIn", "naukri": "Naukri", "indeed": "Indeed", "wellfound": "Wellfound",
    "unstop": "Unstop", "glassdoor": "Glassdoor", "foundit": "Foundit", "synthetic": "Sandbox",
}
SITE_ONLY_KINDS = ("file", "checkbox", "unsupported")

st.set_page_config(page_title="AI Career Agent", layout="wide")
st.title("AI Job Search & Career Agent")


@st.cache_data(ttl=30)
def load_config() -> dict | None:
    try:
        response = httpx.get(f"{API_BASE}/config", timeout=5.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


config = load_config()
if config is None:
    st.error(
        f"Can't reach the backend at {API_BASE}. Start it with `start_backend.bat` "
        "(or `uvicorn main:app --port 8010`), then refresh this page."
    )
    st.stop()

dry_run = config["dry_run"]
threshold = config["match_score_threshold"]
if dry_run:
    st.info("**Dry-run mode** — approving fills the application form and saves a screenshot; nothing is submitted.")
else:
    st.warning("**Live mode** — approving an application submits it to the employer.")

with st.sidebar:
    st.header("Candidate")
    candidate_id = st.text_input("Candidate ID", value=config.get("default_candidate_id") or "demo-candidate")
    st.caption("Autonomous runs use your profile email as the candidate ID.")
    st.divider()
    st.subheader("Quick job search")
    search_query = st.text_input("Job search query (optional)", value="")
    location = st.text_input("Location (optional)", value="")
    resume_file = st.file_uploader("Upload resume (PDF)", type=["pdf"])
    run_clicked = st.button("Run pipeline", type="primary", disabled=resume_file is None)

if run_clicked and resume_file is not None:
    with st.spinner("Parsing resume, searching jobs, scoring matches, finding skill gaps..."):
        try:
            response = httpx.post(
                f"{API_BASE}/pipeline/run",
                data={"candidate_id": candidate_id, "search_query": search_query, "location": location},
                files={"resume": (resume_file.name, resume_file.getvalue(), "application/pdf")},
                timeout=180.0,
            )
            response.raise_for_status()
            st.session_state["result"] = response.json()
        except httpx.HTTPError as e:
            st.error(f"Pipeline failed: {e}")


def _split_detail(application: dict) -> tuple[str, list[str]]:
    detail, _, shots = (application.get("error_log") or "").partition(" | screenshots: ")
    return detail.strip(), [s.strip() for s in shots.split(",") if s.strip() and Path(s.strip()).exists()]


def _show_screenshots(paths: list[str]) -> None:
    for path in paths:
        with st.popover(f"Screenshot: {Path(path).name}"):
            st.image(path)


def _post(path: str, payload: dict | None = None, timeout: float = 60.0) -> tuple[bool, str]:
    try:
        response = httpx.post(f"{API_BASE}{path}", json=payload or {}, timeout=timeout)
        response.raise_for_status()
        return True, response.json().get("status", "done")
    except httpx.HTTPStatusError as e:
        return False, e.response.json().get("detail", str(e))
    except httpx.HTTPError as e:
        return False, str(e)


def _render_answer_form(application: dict) -> None:
    """Approval form for an application held because some required questions had no answer."""
    app_id = application["id"]
    pending = application.get("pending_questions") or []
    site_only = [q for q in pending if q["kind"] in SITE_ONLY_KINDS]

    with st.form(key=f"answers_{app_id}"):
        answers: dict[str, str] = {}
        for q in pending:
            label = f"{q['label'].strip()} {'*' if q['required'] else ''}"
            key = f"{app_id}::{q['key']}"
            if q["kind"] in SITE_ONLY_KINDS:
                what = {"file": "a file upload", "checkbox": "a checkbox"}.get(q["kind"], "a section the agent can't fill")
                st.caption(f"{label} — needs {what}; only possible on the site itself.")
            elif q["kind"] == "select" and q["options"]:
                answers[q["key"]] = st.selectbox(label, q["options"], index=None, placeholder="Choose...", key=key) or ""
            elif q["kind"] == "multiselect" and q["options"]:
                answers[q["key"]] = ";".join(st.multiselect(label, q["options"], key=key))
            elif q["kind"] == "textarea":
                answers[q["key"]] = st.text_area(label, key=key)
            else:
                answers[q["key"]] = st.text_input(label, key=key)

        remember = st.checkbox("Remember these answers for future applications", value=True, key=f"{app_id}::remember")
        submit_label = "Approve & fill (dry run)" if dry_run else "Approve & submit"
        submitted = st.form_submit_button(submit_label, type="primary", disabled=bool(site_only))

    if site_only:
        st.caption("This one needs a step the agent can't do - apply on the site using the link above.")
    if submitted:
        missing = [q["label"] for q in pending if q["required"] and q["kind"] not in SITE_ONLY_KINDS
                   and not answers.get(q["key"], "").strip()]
        if missing:
            st.error("Answer every required question first: " + "; ".join(m.strip() for m in missing))
            return
        with st.spinner("Filling the application form with your answers - this can take up to a minute..."):
            ok, message = _post(f"/applications/{app_id}/answer",
                                {"answers": {k: v for k, v in answers.items() if v}, "remember": remember}, timeout=300.0)
        st.session_state["flash"] = (ok, f"{application.get('job_title', 'Application')}: {message}")
        st.rerun()


def _render_application(application: dict) -> None:
    app_id = application["id"]
    status = application.get("status", "")
    detail, screenshots = _split_detail(application)

    if application.get("job_url"):
        st.markdown(f"[Open the posting]({application['job_url']})")

    if status == "needs_manual":
        if application.get("pending_questions"):
            st.warning("Held for your approval — nothing has been sent. Answer these questions to continue:")
            _render_answer_form(application)
        else:
            st.warning(f"Nothing was sent. {detail}")
            if st.button("Retry", key=f"retry_{app_id}"):
                with st.spinner("Retrying..."):
                    ok, message = _post(f"/applications/{app_id}/answer", {"answers": {}}, timeout=300.0)
                st.session_state["flash"] = (ok, message)
                st.rerun()
        if st.button("Dismiss (not interested)", key=f"dismiss_{app_id}"):
            _post(f"/applications/{app_id}/reject")
            st.rerun()
    elif status == "submitted":
        st.success("Submitted — confirmation detected.")
        if application.get("application_url"):
            st.markdown(f"[Confirmation page]({application['application_url']})")
    elif status == "unconfirmed":
        st.error(f"Submit was clicked but no confirmation was detected — check your email or the posting. {detail}")
    elif status == "dry_run":
        st.info(detail or "Dry run: the form was filled but not submitted.")
    elif status == "verification_required":
        st.warning("Not submitted: after the form was filled, the site asked for a human check (CAPTCHA or a "
                   "security code sent to your email). The agent never completes those - open the posting and "
                   "apply yourself; your answers are in the screenshot below.")
        if st.button("Done / not interested", key=f"verify_done_{app_id}"):
            _post(f"/applications/{app_id}/reject")
            st.rerun()
    elif status == "apply_yourself":
        site = PLATFORM_NAMES.get(application.get("ats_type", ""), "the site")
        st.info(f"Found on {site} with a {application.get('match_score', 0):.0f}% match. "
                f"The agent doesn't act on {site} - open the posting and apply yourself.")
        if st.button("Done / not interested", key=f"lead_done_{app_id}"):
            _post(f"/applications/{app_id}/reject")
            st.rerun()
    elif status == "pending_approval":
        site = PLATFORM_NAMES.get(application.get("ats_type", ""), "the site")
        label = "Why should you be hired? / cover letter (editable)" if application.get("ats_type") == "internshala" \
            else "Cover letter (editable)"
        edited_letter = st.text_area(label, value=application.get("cover_letter_text", ""), key=f"letter_{app_id}")
        st.caption(f"{site}: the agent drafted this for you - apply on the site yourself and paste it in. "
                   "Approving just marks it ready.")
        col_a, col_b, col_c = st.columns(3)
        if col_a.button("Approve", key=f"approve_{app_id}"):
            _post(f"/applications/{app_id}/approve")
            st.rerun()
        if col_b.button("Edit & Approve", key=f"edit_approve_{app_id}"):
            _post(f"/applications/{app_id}/approve", {"cover_letter_text": edited_letter})
            st.rerun()
        if col_c.button("Reject", key=f"reject_{app_id}"):
            _post(f"/applications/{app_id}/reject")
            st.rerun()
    elif status == "blocked":
        st.warning(application.get("blocked_reason", "This job was not eligible for any agent action."))
    elif application.get("execution_strategy") == "assisted_draft":
        st.write("**Drafted cover letter** (submit manually on the actual site):")
        st.text_area("Cover letter", value=application.get("cover_letter_text", ""), key=f"ro_{app_id}")
    elif detail:
        st.error(detail)

    _show_screenshots(screenshots)
    if application.get("cover_letter_text") and status not in ("pending_approval",) \
            and application.get("execution_strategy") != "assisted_draft":
        with st.popover("Cover letter"):
            st.write(application["cover_letter_text"])


def _expander_title(application: dict) -> str:
    score = application.get("match_score")
    score_text = f"{score:.0f}% match" if isinstance(score, (int, float)) else ""
    company = application.get("company") or ""
    title = application.get("job_title") or "Untitled"
    platform = PLATFORM_NAMES.get(application.get("ats_type") or "", application.get("ats_type") or "")
    return " · ".join(p for p in [application.get("status", "").upper(), f"{title} — {company}" if company else title,
                                  platform, score_text] if p)


applications_tab, job_search_tab = st.tabs(["Applications", "Job Search"])

with applications_tab:
    flash = st.session_state.pop("flash", None)
    if flash:
        (st.success if flash[0] else st.error)(flash[1])

    col1, col2, col3 = st.columns([2, 2, 1])
    status_filter = col1.selectbox("Status", STATUSES)
    min_score_filter = col2.number_input("Min match score", min_value=0, max_value=100, value=0)
    if col3.button("Refresh", use_container_width=True):
        st.rerun()

    params = {"candidate_id": candidate_id}
    if status_filter != "(active)":
        params["status"] = status_filter
    if min_score_filter > 0:
        params["min_score"] = min_score_filter

    try:
        resp = httpx.get(f"{API_BASE}/applications", params=params, timeout=30.0)
        resp.raise_for_status()
        applications = resp.json()
    except httpx.HTTPError as e:
        st.error(f"Failed to load applications: {e}")
        applications = []

    if applications:
        counts = Counter(a.get("status") for a in applications)
        metric_cols = st.columns(6)
        for col, (label, status) in zip(metric_cols, [
            ("Need your answers", "needs_manual"), ("Submitted", "submitted"), ("Drafts to send", "pending_approval"),
            ("Apply yourself", "apply_yourself"), ("Unconfirmed", "unconfirmed"), ("Dry runs", "dry_run"),
        ]):
            col.metric(label, counts.get(status, 0))

        held = [a for a in applications if a.get("status") == "needs_manual"]
        drafts = [a for a in applications if a.get("status") == "pending_approval"]
        # Verification-blocked applications first: they're filled-in applications the user only has to finish.
        leads = sorted([a for a in applications if a.get("status") in ("verification_required", "apply_yourself")],
                       key=lambda a: (a.get("status") != "verification_required", -(a.get("match_score") or 0)))
        others = [a for a in applications if a.get("status") not in
                  ("needs_manual", "pending_approval", "apply_yourself", "verification_required")]
        if held:
            st.subheader(f"Needs your approval ({len(held)})")
            for application in held:
                with st.expander(_expander_title(application), expanded=len(held) <= 3):
                    _render_application(application)
        if drafts:
            st.subheader(f"Drafted for you to send ({len(drafts)})")
            st.caption("Internshala, Workday and SmartRecruiters: the agent drafts, you apply on the site.")
            for application in drafts:
                with st.expander(_expander_title(application)):
                    _render_application(application)
        if leads:
            st.subheader(f"Apply yourself ({len(leads)})")
            st.caption("Postings blocked by a site's human check first, then LinkedIn and job-board matches, best "
                       "first. The agent never acts on these sites.")
            for application in leads:
                with st.expander(_expander_title(application)):
                    _render_application(application)
        if others:
            st.subheader("All other applications")
            for application in others:
                with st.expander(_expander_title(application)):
                    _render_application(application)
    else:
        st.info(
            f"No applications for **{candidate_id}** yet. Run `python -m autonomous_apply --location <city> "
            "--resume <your resume.pdf>`, or apply to a match from the Job Search tab."
        )

with job_search_tab:
    result = st.session_state.get("result")
    if result:
        candidate = result.get("candidate") or {}
        matches = result.get("matches") or []
        skill_gaps = result.get("skill_gaps") or []

        st.subheader("Parsed Candidate Profile")
        col1, col2 = st.columns(2)
        with col1:
            st.write("**Skills**", candidate.get("skills", []))
            st.write("**Education**", candidate.get("education_level", ""))
        with col2:
            st.write("**Years of experience**", candidate.get("years_experience", 0))
            st.write("**Location**", candidate.get("location", ""))

        st.subheader("Job Matches")
        if matches:
            for i, m in enumerate(matches):
                job = m.get("job", {})
                score = m.get("overall_score", 0)
                with st.expander(f"{job.get('title', 'Untitled')} — {score:.1f}% match"):
                    st.write(job.get("snippet", ""))
                    if job.get("link"):
                        st.markdown(f"[View listing]({job['link']})")
                    st.json(m.get("components", {}))

                    if score >= threshold:
                        if st.button("Apply", key=f"apply_{i}"):
                            try:
                                resp = httpx.post(
                                    f"{API_BASE}/applications/start",
                                    json={"candidate_id": candidate_id, "job": job, "match_score": score},
                                    timeout=300.0,
                                )
                                resp.raise_for_status()
                                st.success(f"Application started: {resp.json().get('status')}")
                            except httpx.HTTPError as e:
                                st.error(f"Failed to start application: {e}")
                    else:
                        st.caption(f"Below the {threshold:g}% apply threshold")
        else:
            st.info("No job matches found.")

        st.subheader("Skill Gaps")
        if skill_gaps:
            st.write(skill_gaps)
        else:
            st.info("No skill gaps identified.")
    else:
        st.info("Upload a resume in the sidebar and click **Run pipeline** for a quick search. "
                "For applying, use the autonomous CLI - its results appear in the Applications tab.")
