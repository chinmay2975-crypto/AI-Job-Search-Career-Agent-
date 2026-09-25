from pathlib import Path

import streamlit as st
import httpx

API_BASE = "http://localhost:8000"
MATCH_SCORE_THRESHOLD = 70

st.set_page_config(page_title="AI Career Agent", layout="wide")
st.title("AI Job Search & Career Agent")

with st.sidebar:
    st.header("Candidate")
    candidate_id = st.text_input("Candidate ID", value="demo-candidate")
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
                timeout=120.0,
            )
            response.raise_for_status()
            st.session_state["result"] = response.json()
        except httpx.HTTPError as e:
            st.error(f"Pipeline failed: {e}")

job_search_tab, applications_tab = st.tabs(["Job Search", "Applications"])

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

                    if score >= MATCH_SCORE_THRESHOLD:
                        if st.button("Apply", key=f"apply_{i}"):
                            try:
                                resp = httpx.post(
                                    f"{API_BASE}/applications/start",
                                    json={"candidate_id": candidate_id, "job": job, "match_score": score},
                                    timeout=60.0,
                                )
                                resp.raise_for_status()
                                st.success(f"Application started: {resp.json()}")
                            except httpx.HTTPError as e:
                                st.error(f"Failed to start application: {e}")
                    else:
                        st.caption(f"Below the {MATCH_SCORE_THRESHOLD}% apply threshold")
        else:
            st.info("No job matches found.")

        st.subheader("Skill Gaps")
        if skill_gaps:
            st.write(skill_gaps)
        else:
            st.info("No skill gaps identified.")
    else:
        st.info("Upload a resume and click **Run pipeline** to get started.")

with applications_tab:
    st.subheader("Applications")

    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.selectbox(
            "Status",
            [
                "(any)", "draft", "pending_approval", "approved", "submitted", "needs_manual", "unconfirmed",
                "dry_run", "failed", "rejected_by_user", "blocked",
            ],
        )
    with col2:
        min_score_filter = st.number_input("Min match score", min_value=0, max_value=100, value=0)
    with col3:
        if st.button("Refresh"):
            st.rerun()

    params = {"candidate_id": candidate_id}
    if status_filter != "(any)":
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

    if not applications:
        st.info("No applications yet. Apply to a match in the Job Search tab.")

    for application in applications:
        app_id = application["id"]
        status = application.get("status", "")
        ats_type = application.get("ats_type", "")
        score = application.get("match_score", 0)

        title = application.get("job_title") or "Untitled"
        with st.expander(f"{status.upper()} — {title} — {ats_type or 'unclassified'} — {score}% match"):
            if status in ("needs_manual", "unconfirmed", "dry_run"):
                detail, _, shots = (application.get("error_log") or "").partition(" | screenshots: ")
                if status == "needs_manual":
                    st.warning(f"Nothing was sent. {detail}")
                elif status == "unconfirmed":
                    st.error(f"Submit was clicked but no confirmation was detected — check your email/the posting. {detail}")
                else:
                    st.info(detail or "Dry run: the form was filled but not submitted.")
                if application.get("job_url"):
                    st.markdown(f"[Open the original posting]({application['job_url']})")
                for shot in filter(None, (s.strip() for s in shots.split(","))):
                    if Path(shot).exists():
                        st.image(shot, caption=Path(shot).name)
            elif status == "pending_approval":
                edited_letter = st.text_area(
                    "Cover letter (editable)", value=application.get("cover_letter_text", ""), key=f"letter_{app_id}"
                )
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("Approve", key=f"approve_{app_id}"):
                        httpx.post(f"{API_BASE}/applications/{app_id}/approve", json={}, timeout=60.0)
                        st.rerun()
                with col_b:
                    if st.button("Edit & Approve", key=f"edit_approve_{app_id}"):
                        httpx.post(
                            f"{API_BASE}/applications/{app_id}/approve",
                            json={"cover_letter_text": edited_letter},
                            timeout=60.0,
                        )
                        st.rerun()
                with col_c:
                    if st.button("Reject", key=f"reject_{app_id}"):
                        httpx.post(f"{API_BASE}/applications/{app_id}/reject", timeout=30.0)
                        st.rerun()
            elif status == "blocked":
                st.warning(application.get("blocked_reason", "This job was not eligible for any agent action."))
                if application.get("job_title"):
                    st.write(f"**{application['job_title']}**")
                if application.get("job_url"):
                    st.markdown(f"[Open the original posting]({application['job_url']}) to apply manually.")
            elif application.get("execution_strategy") == "assisted_draft":
                st.write("**Drafted cover letter** (submit manually on the actual site):")
                st.text_area("Cover letter", value=application.get("cover_letter_text", ""), key=f"ro_{app_id}")
            else:
                st.write("**Cover letter:**")
                st.text_area("Cover letter", value=application.get("cover_letter_text", ""), key=f"ro2_{app_id}", disabled=True)
                if application.get("application_url"):
                    st.markdown(f"[Application confirmation]({application['application_url']})")
                if application.get("error_log"):
                    st.error(application["error_log"])
