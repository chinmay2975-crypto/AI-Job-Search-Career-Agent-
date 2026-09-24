from agents.state import ApplicationState
from db import get_repository
from services.llm import complete

_DRAFT_PROMPT = """Write a concise, tailored cover letter (3-4 short paragraphs, no placeholders)
for this candidate applying to this job. Do not invent experience the candidate doesn't have.

Candidate resume:
{resume_text}

Candidate skills: {skills}

Job title: {title}
Company: {company}
Job description:
{description}
"""


def run(state: ApplicationState) -> ApplicationState:
    candidate = state.get("candidate", {})
    job = state.get("job", {})

    prompt = _DRAFT_PROMPT.format(
        resume_text=candidate.get("resume_text", ""),
        skills=", ".join(candidate.get("skills", [])),
        title=job.get("title", ""),
        company=job.get("company", ""),
        description=job.get("description", ""),
    )

    cover_letter_text = complete(prompt, temperature=0.4)

    repo = get_repository()
    application_id = state["application_id"]
    repo.update_application(application_id, {"cover_letter_text": cover_letter_text, "status": "draft"})
    repo.add_application_event(application_id, "drafted", "cover letter drafted")

    return {**state, "cover_letter_text": cover_letter_text, "status": "draft"}
