import json

from agents.state import GraphState
from services import resume_parser
from services.llm import complete

_EXTRACTION_PROMPT = """Extract the following fields from this resume as strict JSON, no markdown, no commentary:
{{
  "skills": [list of skill strings],
  "education_level": one of "high_school" | "associate" | "bachelor" | "master" | "phd",
  "years_experience": number,
  "projects": [list of short project description strings],
  "location": string (city, state/country if present, else "")
}}

Resume:
{resume_text}
"""


def run(state: GraphState) -> GraphState:
    resume_text = resume_parser.extract_text(state["resume_bytes"])

    raw = complete(_EXTRACTION_PROMPT.format(resume_text=resume_text), temperature=0.1)
    try:
        fields = json.loads(raw)
    except json.JSONDecodeError:
        fields = {
            "skills": [],
            "education_level": "",
            "years_experience": 0,
            "projects": [],
            "location": "",
        }

    candidate = {
        "candidate_id": state["candidate_id"],
        "resume_text": resume_text,
        **fields,
    }
    return {**state, "candidate": candidate}
