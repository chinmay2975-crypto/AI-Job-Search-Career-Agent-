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
twice. Set `SQLITE_DB_PATH` in `.env` to put it elsewhere.

The file holds your application history and parsed resume, so it is gitignored. To back it up, copy
`data/career_agent.db` while nothing is running (or copy it together with its `-wal`/`-shm` files).

## Running locally

**One-time setup (Windows):** run `install_autostart.bat`. From then on the backend and the UI start
hidden at every login, restart themselves if they crash, and a **Career Agent** shortcut on the desktop
opens the app (starting the servers first if needed). `uninstall_autostart.bat` undoes it.

Without autostart, `start_all.bat` does the same once. Both servers listen on this machine only
(127.0.0.1). Logs: `logs/uvicorn.log`, `logs/streamlit.log`, `logs/launcher.log`. By hand:

```bash
uvicorn main:app --port 8010      # backend - port 8010, so it doesn't clash with other apps on 8000
streamlit run app.py              # frontend, in a separate terminal
```

- UI: http://localhost:8501 — the **Applications** tab shows every application, with the ones waiting
  for your answers at the top.
- API docs (Swagger UI): http://localhost:8010/docs
- To use a different backend port, set `CAREER_AGENT_API_URL` for Streamlit and `SANDBOX_APPLY_URL` in `.env`.

## Autonomous apply

```bash
python -m autonomous_apply --location "Pune" --resume C:\path\to\resume.pdf
# options: --query "python intern"  --platforms greenhouse,lever,internshala,...  --max-jobs 20  --include-remote
```

A deterministic planner ([agents/planner_agent.py](agents/planner_agent.py)) routes between four
specialists ([agents/autonomous_graph.py](agents/autonomous_graph.py)):

1. **Resume** — parses the resume with the existing Resume Agent.
2. **Discovery** — site-restricted web search on every source below (all by default), filtered by
   `--location`, then de-duplicated across sites (a posting listed on several sites is kept once, on the
   site where the agent can do the most).
3. **Matching** — the LLM extracts each posting's required skills/years/education; the existing
   deterministic `score_match` scores it. Auto-apply sources need extractable requirements; board
   postings that only have a search snippet get neutral (not full) skills credit.
4. **Apply** — each posting above `MATCH_SCORE_THRESHOLD` goes through the per-application flow:

| Source | What the agent does | Where it shows up |
|---|---|---|
| Greenhouse, Lever, Ashby, Workable | Fills the real form (resume, cover letter, answers) and clicks Submit; success only on a detected confirmation. Unanswerable questions → held for your approval. | Submitted / Needs your approval |
| Internshala, SmartRecruiters, Workday | Drafts the cover letter ("why should you be hired"); **you** apply on the site. Internshala needs your account and SmartRecruiters blocks bots, so these are never automated. | Drafted for you to send |
| LinkedIn, Naukri, Indeed, Wellfound, Unstop, Glassdoor, Foundit | Lists the posting with its match score and link — nothing else. LinkedIn pages are never opened. | Apply yourself |

How postings are read: Greenhouse, Lever, Ashby, Workable, SmartRecruiters and Workday through their
public job APIs (closed postings are dropped); Internshala from its posting pages (allowed by its
robots.txt, requests spaced out); every other board only from the search result itself.

Cookie banners that block a form are closed with the no-tracking choice ("Decline all" / "Reject");
cookies are never accepted on your behalf. CAPTCHA challenges are never solved or bypassed.

**Setup before the first run**
- `python -m playwright install chromium`
- Copy `candidate_profile.example.yaml` to `candidate_profile.yaml` (gitignored) and fill it in.
  Screening questions about you (work authorization, sponsorship, notice period, CTC, availability,
  shift/hybrid preferences, EEO) are answered **only** from this file and from answers you've approved
  before. The LLM only answers required questions it can prove from your resume (a dropdown answer must
  cite text that actually appears in it).

- `job_type: internship` in the profile limits every search to intern roles (the search asks for
  internships and non-intern postings are dropped); `--job-type any` overrides it for one run. The
  profile's `education` block fills forms that have an Education section.

**Approving held applications.** Any required question the profile and resume can't answer holds the
application as `needs_manual` — nothing is sent. In the Streamlit **Applications** tab, under
*Needs your approval*, answer the listed questions and click **Approve & submit** (or **Approve & fill**
in dry-run mode): the agent re-fills the real form with your answers. With *Remember these answers* on,
the same question on later forms is answered automatically (they only fill gaps — they never override
`candidate_profile.yaml`). Questions that need a file upload or checkbox can only be done on the site.

**Statuses** (Streamlit Applications tab and `logs/applications_audit.csv`)
- `submitted` — confirmation detected.
- `needs_manual` — nothing was sent: waiting for your answers (approve in Streamlit), an unfillable
  field, or a CAPTCHA challenge (challenges are never solved automatically). Also retried on the next run.
- `verification_required` — after the form was filled, the site asked for a human check (CAPTCHA or a
  security code emailed to you). Nothing was sent; the agent never completes these. Apply on the site
  yourself. Not retried automatically (each attempt could email you another code).
- `apply_yourself` — a LinkedIn / job-board posting listed with its score and link.
- `unconfirmed` — Submit was clicked but no confirmation appeared. Check the screenshot / your email.
  Never retried automatically, so it can't produce a duplicate application.
- `dry_run`, `failed` — nothing was sent; retried on the next run.
- `superseded` — an earlier not-sent attempt replaced by a newer one for the same job (hidden by default).

> **`DRY_RUN`** (in `.env`) decides whether Submit is clicked. With `DRY_RUN=true` the agent still fills
> every form and saves a screenshot to `logs/screenshots/`, but submits nothing — use it after changing
> your profile. Note that attaching the resume may let the ATS parse it even in a dry run; no application
> is created. There is no daily limit; `--max-jobs` (default 20) caps how many applications one run makes.

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
