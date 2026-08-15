import streamlit as st
import httpx

API_BASE = "http://localhost:8000"

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
        for m in matches:
            job = m.get("job", {})
            with st.expander(f"{job.get('title', 'Untitled')} — {m.get('overall_score', 0):.1f}% match"):
                st.write(job.get("snippet", ""))
                if job.get("link"):
                    st.markdown(f"[View listing]({job['link']})")
                st.json(m.get("components", {}))
    else:
        st.info("No job matches found.")

    st.subheader("Skill Gaps")
    if skill_gaps:
        st.write(skill_gaps)
    else:
        st.info("No skill gaps identified.")
else:
    st.info("Upload a resume and click **Run pipeline** to get started.")
