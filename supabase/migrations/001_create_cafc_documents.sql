create extension if not exists pgcrypto;

create table if not exists public.cafc_documents (
  id uuid primary key default gen_random_uuid(),
  opinion_date date not null,
  release_date text not null,
  appeal_number text not null,
  origin text not null check (origin in ('PTO', 'DCT', 'ITC')),
  source_tribunal text not null,
  document_type text not null,
  case_name text not null,
  status text,
  disposition text,
  holding text not null,
  why_it_matters text,
  key_points jsonb not null default '[]'::jsonb,
  tags text[] not null default '{}'::text[],
  issue_tags text[] not null default '{}'::text[],
  holding_tags text[] not null default '{}'::text[],
  technology_area text,
  procedural_posture text,
  summary_text text not null,
  summary_mode text,
  source_url text not null,
  pdf_url text not null,
  source_file_path text not null,
  local_pdf text,
  local_text text,
  cafc_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  fts tsvector generated always as (
    setweight(to_tsvector('english', coalesce(case_name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(holding, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(summary_text, '')), 'B') ||
    setweight(to_tsvector('english', array_to_string(tags, ' ')), 'A') ||
    setweight(to_tsvector('english', array_to_string(issue_tags, ' ')), 'A') ||
    setweight(to_tsvector('english', array_to_string(holding_tags, ' ')), 'A') ||
    setweight(to_tsvector('english', coalesce(source_tribunal, '')), 'C')
  ) stored,
  unique (source_file_path)
);

create index if not exists cafc_documents_opinion_date_idx on public.cafc_documents (opinion_date desc);
create index if not exists cafc_documents_origin_idx on public.cafc_documents (origin);
create index if not exists cafc_documents_tags_idx on public.cafc_documents using gin (tags);
create index if not exists cafc_documents_issue_tags_idx on public.cafc_documents using gin (issue_tags);
create index if not exists cafc_documents_holding_tags_idx on public.cafc_documents using gin (holding_tags);
create index if not exists cafc_documents_fts_idx on public.cafc_documents using gin (fts);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_cafc_documents_updated_at on public.cafc_documents;
create trigger set_cafc_documents_updated_at
before update on public.cafc_documents
for each row execute function public.set_updated_at();

create or replace function public.search_cafc_documents(
  search_query text default null,
  filter_origins text[] default null,
  filter_tags text[] default null,
  start_date date default null,
  end_date date default null
)
returns table (
  id uuid,
  opinion_date date,
  appeal_number text,
  origin text,
  source_tribunal text,
  document_type text,
  case_name text,
  status text,
  disposition text,
  holding text,
  why_it_matters text,
  tags text[],
  issue_tags text[],
  holding_tags text[],
  pdf_url text,
  rank real
)
language sql
stable
as $$
  select
    d.id,
    d.opinion_date,
    d.appeal_number,
    d.origin,
    d.source_tribunal,
    d.document_type,
    d.case_name,
    d.status,
    d.disposition,
    d.holding,
    d.why_it_matters,
    d.tags,
    d.issue_tags,
    d.holding_tags,
    d.pdf_url,
    case
      when nullif(search_query, '') is null then 0::real
      else ts_rank(d.fts, websearch_to_tsquery('english', search_query))
    end as rank
  from public.cafc_documents d
  where (nullif(search_query, '') is null or d.fts @@ websearch_to_tsquery('english', search_query))
    and (filter_origins is null or cardinality(filter_origins) = 0 or d.origin = any(filter_origins))
    and (filter_tags is null or cardinality(filter_tags) = 0 or (d.tags || d.issue_tags || d.holding_tags) && filter_tags)
    and (start_date is null or d.opinion_date >= start_date)
    and (end_date is null or d.opinion_date <= end_date)
  order by
    case when nullif(search_query, '') is null then d.opinion_date end desc,
    rank desc,
    d.opinion_date desc,
    d.case_name asc;
$$;

alter table public.cafc_documents enable row level security;

-- This table is intended for server-side writes using SUPABASE_SERVICE_ROLE_KEY.
-- Add explicit SELECT policies only if/when you decide to expose database reads to a public app.
