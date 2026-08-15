# AI Job Search & Career Agent

An agent pipeline that takes a candidate's resume, searches live job listings, scores how well
each job matches the candidate, and identifies skill gaps — orchestrated with LangGraph, served
via FastAPI, and driven from a Streamlit UI.

## How it works

```
Resume PDF → Resume Agent → Job Search Agent → Matching Agent → Skill Gap Agent
```

1. **Resume Agent** — extracts text from the uploaded PDF (`pdfplumber`/`pypdf`) and uses an LLM
   (Groq + LLaMA 3.3 70B) to structure it into skills, education level, years of experience,
   projects, and location.
2. **Job Search Agent** — queries the [Serper API](https://serper.dev) (Google search wrapper)
   for live job listings based on the candidate's skills and location.
3. **Matching Agent** — scores each job with a deterministic weighted formula (not LLM-generated):
   Skills 40% · Education 15% · Experience 20% · Projects 10% · Location 5% · Job requirements 10%,
   using TF-IDF + cosine similarity for the text-based components.
4. **Skill Gap Agent** — asks the LLM to compare the candidate's skills against the top-matched
   job descriptions and surface missing/weak skills.

The pipeline is wired as a `LangGraph` `StateGraph` (see [agents/graph.py](agents/graph.py)) with
checkpointing so state persists across steps.

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | Streamlit |
| Backend API | FastAPI + Uvicorn |
| Agent orchestration | LangGraph |
| LLM | Groq (LLaMA 3.3 70B) |
| Job search | Serper API |
| Matching | scikit-learn (TF-IDF + cosine) + deterministic weighted scoring |
| Resume parsing | pypdf / pdfplumber |
| Memory / persistence | Repository interface — SQLite locally, swappable for Supabase Postgres |

Full rationale in [career-agent-tech-stack.md](career-agent-tech-stack.md).

## Project structure

```
main.py                 FastAPI app entrypoint
app.py                  Streamlit UI entrypoint
agents/                 LangGraph state schema, per-agent nodes, and graph wiring
services/               LLM client, resume parsing, job search, matching logic
db/                     Repository interface + SQLite/Supabase implementations
api/                    FastAPI routes
tests/                  Unit tests per agent node (mocked LLM/Serper calls)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

cp .env.example .env
# fill in GROQ_API_KEY and SERPER_API_KEY in .env
```

`DATABASE_URL` can be left blank — the app falls back to a local SQLite file
(`career_agent.db`) when it's unset.

## Running locally

Start the backend:

```bash
uvicorn main:app --reload
```

API docs (Swagger UI): http://localhost:8000/docs

Start the frontend, in a separate terminal:

```bash
streamlit run app.py
```

UI: http://localhost:8501

Upload a resume PDF in the sidebar, optionally set a job search query/location, and click
**Run pipeline**.

## Tests

```bash
pytest tests/
```

Each agent node is tested in isolation with mocked LLM and Serper calls, so tests run without
API keys.

## Status / roadmap

- ✅ Resume parsing, job search, matching, skill gap agents — wired end-to-end
- ⏳ Resume evolution (regenerate an improved resume PDF via ReportLab) — not yet built
- ⏳ Interview Agent (dual-LLM mock interview: interviewer + judge) — deferred to a later milestone
- ⏳ Supabase-backed persistence — repository interface is in place, SQLite is the only
  implementation so far
