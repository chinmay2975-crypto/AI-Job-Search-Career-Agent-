# AI Job Search & Career Agent

An agent pipeline that takes a candidate's resume, searches live job listings, scores how well
each job matches the candidate, and identifies skill gaps — orchestrated with LangGraph, served
via FastAPI, and driven from a Streamlit UI.

## How it works

```
Resume PDF → Resume Agent → Job Search Agent → Matching Agent → Skill Gap Agent
```

1. **Resume Agent** — extracts text from the uploaded PDF (`pdfplumber`/`pypdf`) and uses an LLM
   (Groq, `openai/gpt-oss-120b`) to structure it into skills, education level, years of experience,
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
| LLM | Groq (`openai/gpt-oss-120b`; LLaMA 3.3 70B was retired from the account) |
| Job search | Serper API; direct ATS discovery via Greenhouse / Lever / Workday public job APIs |
| Matching | scikit-learn (TF-IDF + cosine) + deterministic weighted scoring |
| Resume parsing | pypdf / pdfplumber |
| Applying | Playwright (Chromium) |
| Memory / persistence | Local SQLite (`data/career_agent.db`) behind a repository interface |

Full rationale in [career-agent-tech-stack.md](career-agent-tech-stack.md).

## Project structure

```
main.py                 FastAPI app entrypoint
app.py                  Streamlit UI entrypoint
autonomous_apply.py     CLI for the autonomous discover -> match -> apply flow
agents/                 LangGraph state schema, per-agent nodes, and graph wiring
services/               LLM client, resume parsing, job search/discovery, matching, form answers, submitters
db/                     Repository interface + SQLite implementation (schema created on first use)
data/                   The SQLite database (gitignored)
api/                    FastAPI routes
tests/                  Unit tests (mocked LLM/Serper/browser calls)
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

cp .env.example .env
# fill in GROQ_API_KEY and SERPER_API_KEY in .env
```

### Data

Everything is stored in one local SQLite file, `data/career_agent.db`, created automatically on first
use. There is no database server to run and no database credentials. The path is resolved from the
project folder rather than the directory you run a command from, so the API, Streamlit, and the CLI
always share the same database — that matters because it is what prevents applying to the same job
twice and enforces the daily cap. Set `SQLITE_DB_PATH` in `.env` to put it elsewhere.

The file holds your application history and parsed resume, so it is gitignored. To back it up, copy
`data/career_agent.db` while nothing is running (or copy it together with its `-wal`/`-shm` files).

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

## Autonomous apply

```bash
python -m autonomous_apply --location "Pune" --resume C:\path\to\resume.pdf
# options: --query "python developer"  --platforms greenhouse,lever,workday  --max-jobs 20  --include-remote
```

A deterministic planner ([agents/planner_agent.py](agents/planner_agent.py)) routes between four
specialists ([agents/autonomous_graph.py](agents/autonomous_graph.py)):

1. **Resume** — parses the resume with the existing Resume Agent.
2. **Discovery** — site-restricted search for postings directly on Greenhouse, Lever, and Workday
   (never LinkedIn or aggregators), enriched through each platform's public job API (closed postings
   are dropped), then filtered by `--location`.
3. **Matching** — the LLM extracts each posting's required skills/years/education; the existing
   deterministic `score_match` scores it. Postings with no extractable requirements are skipped.
4. **Apply** — for each posting above `MATCH_SCORE_THRESHOLD`, runs the per-application flow
   (classify ATS → draft cover letter → submit or hand off):
   - **Greenhouse / Lever**: fills the real form with Playwright, attaches the resume and a cover
     letter PDF, and clicks Submit. Success is recorded only when a confirmation is detected.
   - **Workday**: drafted only — lands as `pending_approval` for you to submit by hand.

**Setup before the first run**
- `python -m playwright install chromium`
- Copy `candidate_profile.example.yaml` to `candidate_profile.yaml` (gitignored) and fill it in.
  Screening questions about you (work authorization, sponsorship, notice period, CTC, availability,
  shift/hybrid preferences, EEO) are answered **only** from this file; add `custom_answers` rules for
  questions you see in the audit log. The LLM only answers required questions it can prove from your
  resume (a dropdown answer must cite text that actually appears in it).

**Statuses** (Streamlit Applications tab and `logs/applications_audit.csv`)
- `submitted` — confirmation detected.
- `needs_manual` — nothing was sent: an unanswerable required question, an unfillable field, or a
  CAPTCHA challenge (challenges are never solved automatically). Retried on the next run, so adding the
  missing `custom_answers` and re-running is enough.
- `unconfirmed` — Submit was clicked but no confirmation appeared. Check the screenshot / your email.
  Never retried automatically, so it can't produce a duplicate application.
- `dry_run`, `failed` — nothing was sent; retried on the next run.

> **`DRY_RUN`** (in `.env`) decides whether Submit is clicked. With `DRY_RUN=true` the agent still fills
> every form and saves a screenshot to `logs/screenshots/`, but submits nothing — use it after changing
> your profile. Note that attaching the resume may let the ATS parse it even in a dry run; no application
> is created. `AUTO_SUBMIT_DAILY_CAP` (default 5) limits real submissions per day.

## Tests

```bash
pytest tests/
```

Each agent node is tested in isolation with mocked LLM and Serper calls, so tests run without
API keys.

## Status / roadmap

- ✅ Resume parsing, job search, matching, skill gap agents — wired end-to-end
- ✅ Application Agent with human approval, local SQLite persistence, synthetic sandbox
- ✅ Autonomous apply: Greenhouse/Lever discovery + submission, Workday discovery + drafting
- ⏳ Resume evolution (regenerate an improved resume PDF via ReportLab) — not yet built
- ⏳ Interview Agent (dual-LLM mock interview: interviewer + judge) — deferred to a later milestone
