-- Migration 002: Add PTAB Director Decision support to cafc_documents
--
-- Run in the Supabase SQL editor for project ijgjlbrcccwbdydrpgzq.
-- WARNING: Step 3 (FTS column rebuild) is a table rewrite and will briefly
--          lock the table. Run during low-traffic hours.

-- ─── 1. New PTAB-specific columns ──────────────────────────────────────────

alter table public.cafc_documents
  add column if not exists source_type text default 'cafc'
    check (source_type in ('cafc', 'ptab_director')),
  add column if not exists status_category text,
  add column if not exists trial_numbers text[] default '{}',
  add column if not exists is_omnibus boolean default false;

update public.cafc_documents set source_type = 'cafc' where source_type is null;

create index if not exists cafc_documents_source_type_idx
  on public.cafc_documents (source_type);

-- ─── 2. Relax NOT NULL constraints PTAB rows cannot always satisfy ──────────

-- release_date: PTAB rows use decision_date as a stand-in
-- document_type: may be absent from API response
-- source_url: PDF URL used instead; may be null when no PDF is attached
-- pdf_url: some PTAB decisions lack a directly accessible PDF link
-- appeal_number stays NOT NULL — ptab_daily.py always supplies a trial_number.

alter table public.cafc_documents
  alter column release_date  drop not null,
  alter column document_type drop not null,
  alter column source_url    drop not null,
  alter column pdf_url       drop not null;

-- ─── 3. Rebuild FTS generated column to index PTAB fields ──────────────────
-- (Table rewrite — run off-hours)

alter table public.cafc_documents drop column fts;

alter table public.cafc_documents
  add column fts tsvector generated always as (
    setweight(to_tsvector('english', coalesce(case_name,       '')), 'A') ||
    setweight(to_tsvector('english', coalesce(holding,         '')), 'A') ||
    setweight(to_tsvector('english', coalesce(summary_text,    '')), 'B') ||
    setweight(to_tsvector('english', array_to_string(tags,          ' ')), 'A') ||
    setweight(to_tsvector('english', array_to_string(issue_tags,    ' ')), 'A') ||
    setweight(to_tsvector('english', array_to_string(holding_tags,  ' ')), 'A') ||
    setweight(to_tsvector('english', coalesce(source_tribunal, '')), 'C') ||
    setweight(to_tsvector('english', coalesce(status_category, '')), 'B') ||
    setweight(to_tsvector('english', array_to_string(trial_numbers, ' ')), 'B')
  ) stored;

create index if not exists cafc_documents_fts_idx
  on public.cafc_documents using gin (fts);

-- ─── 4. Replace search RPC to support source_type filtering ────────────────

create or replace function public.search_cafc_documents(
  search_query    text    default null,
  filter_origins  text[]  default null,
  filter_tags     text[]  default null,
  start_date      date    default null,
  end_date        date    default null,
  filter_source   text    default null
)
returns table (
  id              uuid,
  opinion_date    date,
  appeal_number   text,
  origin          text,
  source_tribunal text,
  document_type   text,
  case_name       text,
  status          text,
  disposition     text,
  holding         text,
  why_it_matters  text,
  tags            text[],
  issue_tags      text[],
  holding_tags    text[],
  pdf_url         text,
  source_type     text,
  status_category text,
  trial_numbers   text[],
  is_omnibus      boolean,
  rank            real
)
language sql stable as $$
  select
    d.id, d.opinion_date, d.appeal_number, d.origin, d.source_tribunal,
    d.document_type, d.case_name, d.status, d.disposition, d.holding,
    d.why_it_matters, d.tags, d.issue_tags, d.holding_tags, d.pdf_url,
    d.source_type, d.status_category, d.trial_numbers, d.is_omnibus,
    case
      when nullif(search_query, '') is null then 0::real
      else ts_rank(d.fts, websearch_to_tsquery('english', search_query))
    end as rank
  from public.cafc_documents d
  where (nullif(search_query, '') is null
           or d.fts @@ websearch_to_tsquery('english', search_query))
    and (filter_origins is null or cardinality(filter_origins) = 0
           or d.origin = any(filter_origins))
    and (filter_tags is null or cardinality(filter_tags) = 0
           or (d.tags || d.issue_tags || d.holding_tags) && filter_tags)
    and (start_date is null or d.opinion_date >= start_date)
    and (end_date   is null or d.opinion_date <= end_date)
    and (filter_source is null or d.source_type = filter_source)
  order by
    case when nullif(search_query, '') is null then d.opinion_date end desc,
    rank desc,
    d.opinion_date desc,
    d.case_name asc;
$$;
