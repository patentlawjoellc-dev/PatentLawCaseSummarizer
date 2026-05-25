# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Daily Python script that scrapes the CAFC opinions page, downloads new PDFs, extracts text, generates AI summaries with Claude Sonnet, and upserts records into Supabase (`cafc_documents` table). The Next.js web app reads from Supabase to power the `/blog` page.

## Commands

```bash
python scripts/cafc_daily.py                       # today's opinions (scrape + summarize + sync)
python scripts/cafc_daily.py --date 2026-05-01     # specific date (scrapes live CAFC page)
python scripts/cafc_daily.py --resync              # re-summarize all existing dates from saved files
python scripts/cafc_daily.py --rebuild-only --sync-supabase  # rebuild HTML + push to Supabase
python scripts/cafc_daily.py --no-ai              # fallback summaries, no Claude API call
python scripts/cafc_daily.py --no-supabase        # skip Supabase upsert
python scripts/cafc_daily.py --force              # re-download PDFs even if local copies exist
```

Runs daily at ~11:30 AM Eastern via Windows Task Scheduler (`scripts/register_daily_task.ps1`).

**Important:** Use `--resync` (not `--date`) when reprocessing historical dates. `--date` scrapes the live CAFC page which may no longer list old opinions — running it on a historical date would overwrite good summaries with an empty result.

## Pipeline (`cafc_daily.py`)

1. `parse_opinion_rows()` — scrape CAFC opinions table, filter for `PTO` / `DCT` / `ITC` origins
2. `download_pdf()` + `extract_text()` — fetch PDF, extract via `pdfplumber` + `ftfy`
3. `summarize_with_claude()` — call Claude Sonnet; returns structured JSON with 20 topic tags
4. `enrich_summary()` — normalize `origin` to 3-letter token, merge Claude tags with keyword-inferred tags from `infer_tags()`
5. `sync_supabase()` — upsert all records to `cafc_documents` (unique on `source_file_path`)
6. `write_post()` / `render_index()` — write Markdown + HTML static output

**`resync_day()`** — safe re-summarization that reads from `data/YYYY-MM-DD/metadata.json` + saved text files instead of scraping the live CAFC page.

## Tag System

20 topic tags defined in `ALL_TAGS`. Claude is instructed to use only these exact strings. `infer_tags()` also keyword-matches text to add tags not caught by Claude. Origin (`PTO`/`DCT`/`ITC`) is tracked separately — **do not add origin-based tags**.

```python
ALL_TAGS = [
    "§ 101", "§ 102", "§ 103", "§ 112(a)", "§ 112(b)", "§ 112(f)",
    "claim construction", "infringement", "damages", "attorney fees / sanctions",
    "domestic industry", "exclusion order", "cease & desist",
    "remand", "waiver / forfeiture", "claim preclusion", "Rule 36 affirmance",
    "standing / jurisdiction", "injunction / stay", "Hatch-Waxman / ANDA",
]
```

When changing this list, also update `ALL_TAGS` in `Web Site Design/patent-law-university/src/components/blog/blog-client.tsx` and re-run `--resync --no-supabase` then `--rebuild-only --sync-supabase`.

## Origin Normalization

The DB `cafc_documents.origin` column has a CHECK constraint: `origin IN ('PTO', 'DCT', 'ITC')`. Claude sometimes returns verbose strings. `enrich_summary()` normalizes via prefix matching; if no prefix matches it falls back to `row.origin` (the authoritative value from the scraped table). Never remove this fallback.

## Supabase Schema

Table: `public.cafc_documents` — see `supabase/migrations/001_create_cafc_documents.sql`.
- Full-text search via `fts tsvector` (populated by trigger)
- RLS: writes require service role key; reads are public
- Search RPC: `search_cafc_documents(search_query, filter_origins, filter_tags, start_date, end_date)`

## Environment

`.env` (git-ignored):
```
ANTHROPIC_API_KEY=...
CAFC_SUMMARY_MODEL=claude-sonnet-4-6
SUPABASE_URL=https://ijgjlbrcccwbdydrpgzq.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
NEXT_PUBLIC_SITE_URL=https://patentlawprofessor.com
DIGEST_SECRET=...          # triggers /api/admin/send-digest after sync
```
