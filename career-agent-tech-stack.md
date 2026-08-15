# AI Job Search & Career Agent — Tech Stack

## Frontend
- **Streamlit** (recommended) — fastest to build, pure Python, no separate frontend cycle
- Alternative: **Next.js 14** — better UI polish (dashboard, mock interview, resume evolution) if you have time

## Backend / API
- **FastAPI** — REST API layer connecting frontend to agents (`main.py`)
- **Uvicorn** — local dev server (`uvicorn main:app --reload`)

## Agent Orchestration
- **LangGraph** — StateGraph with multiple nodes (Resume Agent, Job Search Agent, Skill Gap Agent, Matching Agent, Interview Agent), conditional edges for routing, checkpointing for state persistence
- Dual-LLM pattern option: separate "Interviewer" (temp=0.4) and "Judge/Evaluator" (temp=0.1) nodes for agent-to-agent interaction

## LLMs
- **Groq + LLaMA 3.3 70B** — fast, low-cost inference for agent reasoning
- **Gemini 2.5 Flash** — alternative, useful for career-recommendation-style summarization
- Access via `.env` (`GROQ_API_KEY` / `GEMINI_API_KEY`), never committed

## Database
- **PostgreSQL via Supabase** — candidate profile, skills, education, projects, applications, interview history, weak areas
- Same client/schema pattern works for local dev and production (no migration later)

## Memory Layer
- Postgres tables (preferred over a flat `memory.json`) for persistent candidate state across sessions:
  - Skills, Education, Projects, Applications, Interview History, Weak Areas, Learning Progress, Career Preferences

## Resume Handling
- **pypdf / pdfplumber** — extract text from uploaded resume PDFs
- **ReportLab** — generate/regenerate resume PDFs (resume evolution feature)

## Skill / Job Matching
- **TF-IDF + cosine similarity** — keyword/skill matching between resume and job description (reuse from Resume Analyzer)
- Deterministic weighted scoring in plain Python (not LLM-generated):
  - Skills 40% · Education 15% · Experience 20% · Projects 10% · Location 5% · Job requirements 10%
- LLM only *explains* the score — doesn't invent it

## Job Search / Tool Calling
- **Serper API** (Google search wrapper) for live listings, or
- A **synthetic sample job dataset** (10 jobs) — recommended for a scoped, graduate-level build to avoid real job-board integration complexity

## Local Development
| Component | Local setup |
|---|---|
| Backend | `uvicorn main:app --reload` → `localhost:8000` (docs at `/docs`) |
| Frontend | `streamlit run app.py` → `localhost:8501` |
| Database | Supabase free-tier project (cloud, but usable from local dev — no local Postgres install needed) |
| LLM calls | Direct API calls via `.env`, works identically local or deployed |
| Agents | Run in-process inside FastAPI; test nodes individually before wiring into endpoints |

## Deployment (later)
- **GCP Cloud Run** — same target as your Smart Kitchen project, no new deployment learning curve

## Optional / Stretch
- Vector DB + embeddings for RAG-based semantic matching (beyond TF-IDF)
- Web Speech API for voice-based mock interviews

---

### Recommended minimal stack (least new tooling for you)
FastAPI + LangGraph + Groq/LLaMA 3.3 70B + PostgreSQL (Supabase) + Streamlit + TF-IDF/cosine matching + Cloud Run — nearly all reused from Smart Kitchen, Resume Analyzer, and the Finance Agent.
