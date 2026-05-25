-- Migration 003: Add ITC commission and PTAB precedential support
-- Run in Supabase SQL editor for project ijgjlbrcccwbdydrpgzq.

-- 1. Extend source_type CHECK to include new pipeline values
-- Migration 002 added source_type as an inline column-level CHECK, which PostgreSQL
-- auto-names cafc_documents_source_type_check. Drop both the numbered and unnumbered
-- variants defensively to handle either naming outcome.
alter table public.cafc_documents
  drop constraint if exists cafc_documents_source_type_check;
alter table public.cafc_documents
  drop constraint if exists cafc_documents_source_type_check1;

alter table public.cafc_documents
  add constraint cafc_documents_source_type_check
    check (source_type in ('cafc', 'ptab_director', 'itc_commission', 'ptab_precedential'));

-- 2. Extend origin CHECK to allow USITC (for ITC commission decisions)
alter table public.cafc_documents
  drop constraint if exists cafc_documents_origin_check;

alter table public.cafc_documents
  add constraint cafc_documents_origin_check
    check (origin in ('PTO', 'DCT', 'ITC', 'USITC'));

-- 3. Add designation_type column (precedential/informative for ptab_precedential rows)
alter table public.cafc_documents
  add column if not exists designation_type text
    check (designation_type in ('precedential', 'informative'));

comment on column public.cafc_documents.designation_type is
  'For source_type=ptab_precedential: designation by USPTO Director (precedential or informative)';

-- 4. Index for filtering by designation_type
create index if not exists cafc_documents_designation_type_idx
  on public.cafc_documents (designation_type)
  where designation_type is not null;
