create extension if not exists pgcrypto;

create table if not exists candidates (
    id text primary key,
    name text,
    resume_text text,
    -- profile jsonb holds skills, education_level, years_experience, projects, location,
    -- weak_areas, career_preferences - kept as one flexible blob rather than a table per field
    resume_metadata jsonb,
    created_at timestamptz not null default now()
);

create table if not exists graph_checkpoints (
    thread_id text primary key,
    data jsonb not null,
    updated_at timestamptz not null default now()
);

create table if not exists jobs (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    company text,
    url text not null unique,
    description text,
    location text,
    ats_type text,           -- greenhouse | lever | workday | linkedin | indeed | synthetic | unknown
    source text not null,    -- 'serper' | 'synthetic'
    created_at timestamptz not null default now()
);

create table if not exists applications (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references jobs(id),
    candidate_id text not null references candidates(id),
    match_score numeric,
    ats_type text,
    execution_strategy text,  -- auto_submit | assisted_draft | blocked
    status text not null default 'draft',
    -- draft | pending_approval | approved | submitted | failed | rejected_by_user | blocked
    cover_letter_text text,
    thread_id text,           -- LangGraph checkpoint thread id for this application's graph run
    submitted_at timestamptz,
    application_url text,
    error_log text,
    created_at timestamptz not null default now()
);

create table if not exists application_events (
    id uuid primary key default gen_random_uuid(),
    application_id uuid not null references applications(id),
    event_type text not null, -- drafted | approved | blocked | submit_attempted | submit_confirmed | submit_failed | rejected
    detail text,
    created_at timestamptz not null default now()
);

create index if not exists idx_applications_job_id on applications(job_id);
create index if not exists idx_applications_candidate_id on applications(candidate_id);
create index if not exists idx_application_events_application_id on application_events(application_id);
